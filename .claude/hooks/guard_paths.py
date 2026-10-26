"""Claude Code PreToolUse hook: block file deletions and writes outside the project.

Enforces two CLAUDE.md guardrails in code, so they hold even if an instruction is missed:
"Never delete files" and "Never allow any agent or code to modify anything outside of the
current directory".

Protocol (Claude Code hooks): the planned tool call arrives as JSON on stdin, with
``tool_name``, ``tool_input`` and ``cwd``. Exit 0 allows the call. Exit 2 blocks it, and
stderr is shown to Claude as the reason, so it can change course.

What is checked:
    * **File tools** (Write, Edit, MultiEdit, NotebookEdit): the target path must resolve
      inside the project. Exact, because the tool names the path.
    * **Shell commands** (Bash, PowerShell): any deletion command or deletion call is
      blocked. Redirect targets (``>``, ``>>``) and the arguments of file-writing commands
      (``cp``, ``mv``, ``touch``, ``Set-Content``, ...) must resolve inside the project.
      This is a best-effort net, not a sandbox: a path built at run time (a variable, or a
      script that writes files itself) can't be seen in the command text.

Design decisions:
    * **Fail closed.** If the hook can't parse its input or crashes, it blocks the call
      (exit 2) instead of letting an unchecked call through.
    * **Standard library only**, and no ``os`` (CLAUDE.md). It never imports the project
      package, so a broken app can't disable the guard.
"""

import json
import re
import sys
from pathlib import Path

#: The project root: this file lives in ``<project>/.claude/hooks/``.
PROJECT = Path(__file__).resolve().parents[2]

FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

#: Commands that delete, matched as the first word of a command segment (lowercase,
#: without a path or ``.exe``). Includes PowerShell aliases (``ri``, ``rd``, ``del``...).
DELETE_COMMANDS = {"rm", "rmdir", "del", "erase", "rd", "unlink", "shred", "remove-item", "ri"}

#: Deletions that can appear anywhere in a command: Python and .NET APIs, ``find -delete``
#: and git commands that remove files. Written with character classes so this file
#: doesn't trip the project's own scan for deletion calls.
DELETE_ANYWHERE = re.compile(
    r"[.]unlink[(]|[.]rmdir[(]|rmtree|os[.]remove|::Delete[(]|\s-delete\b|\bgit\s+(rm|clean)\b", re.I)

#: Commands whose non-option arguments are files they create or change.
WRITE_COMMANDS = {"cp", "mv", "tee", "touch", "mkdir", "md", "ln", "install", "rsync", "dd", "copy",
                  "move", "xcopy", "robocopy", "out-file", "set-content", "add-content", "sc", "ac",
                  "new-item", "ni", "copy-item", "cpi", "move-item", "mi", "rename-item", "rni"}

#: Words that run the next word as the real command (``sudo rm``, ``xargs rm``).
PREFIXES = {"sudo", "xargs", "exec", "command", "env", "nohup", "time", "."}

#: Device "files" that are safe to redirect to.
SAFE_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "nul", "$null"}

#: Paths given through a variable for a place outside the project (home, temp, app data).
OUTSIDE_VARIABLE = re.compile(r"^(~|\$\{?(HOME|TMP|TEMP|TMPDIR|USERPROFILE|APPDATA|LOCALAPPDATA)\b|%\w+%|\$env:)",
                              re.I)

#: Splits a command line into segments: ``&&``, ``||``, ``;``, ``|``, newlines, subshells,
#: and a lone ``&`` (background job, or PowerShell's call operator) but not ``2>&1``/``&>``.
SEGMENT_SPLIT = re.compile(r"&&|\|\||(?<![>&])&(?![>&])|[;|\n`]|\$\(|[(){}]")

#: A redirect and its target: ``>``, ``>>``, ``2>``, ``*>`` (but not ``>&1``).
REDIRECT = re.compile(r"[0-9*]?>>?\s*(?!&)([^\s;&|<>]+)")


def is_outside(raw, cwd):
    """Decide whether a path written in a command or tool call resolves outside the project.

    Args:
        raw: The path as written: absolute, relative (to ``cwd``), Git Bash style
            (``/c/Users/...``), ``~``-prefixed or variable-prefixed.
        cwd: The directory Claude Code reports the command runs in.

    Returns:
        ``True`` if the path is outside :data:`PROJECT` (or points outside through a
        variable such as ``$HOME``), ``False`` if it is inside or a safe device.
    """
    path = raw.strip("'\"")
    if not path or path.lower() in SAFE_TARGETS:
        return False
    if OUTSIDE_VARIABLE.match(path):
        return True
    # Git Bash spells C:\Users as /c/Users.
    drive = re.match(r"^/([a-zA-Z])(/|$)", path)
    if drive:
        path = f"{drive.group(1)}:/{path[3:]}"
    p = Path(path)
    resolved = (p if p.is_absolute() or p.anchor else Path(cwd) / p).resolve()
    # Windows can return the extended-length form (\\?\C:\...); compare the plain form.
    resolved = Path(str(resolved).removeprefix("\\\\?\\"))
    return not resolved.is_relative_to(PROJECT)


def command_words(segment):
    """Split one command segment into words, dropping env assignments and runner prefixes.

    Args:
        segment: One piece of a command line (see :data:`SEGMENT_SPLIT`).

    Returns:
        The list of words, starting at the real command. Quotes are left on the words;
        :func:`is_outside` strips them.
    """
    words = segment.split()
    while words and (re.match(r"^\w+=", words[0]) or words[0].lower() in PREFIXES):
        words = words[1:]
    return words


def command_name(word):
    """Normalize a command word: ``/usr/bin/rm`` or ``RM.EXE`` becomes ``rm``."""
    return re.split(r"[\\/]", word.strip("'\""))[-1].lower().removesuffix(".exe")


def check_shell(command, cwd):
    """Find the first reason to block a shell command.

    Args:
        command: The full command text.
        cwd: The directory the command runs in.

    Returns:
        A user-facing reason, or ``None`` if the command may run.
    """
    if DELETE_ANYWHERE.search(command):
        return "deleting files is not allowed (CLAUDE.md: never delete files)."
    for segment in SEGMENT_SPLIT.split(command):
        words = command_words(segment)
        if not words:
            continue
        name = command_name(words[0])
        if name in DELETE_COMMANDS:
            return f"`{name}` deletes files, which is not allowed (CLAUDE.md: never delete files)."
        targets = REDIRECT.findall(segment)
        if name in WRITE_COMMANDS:
            targets += [w.split("=", 1)[-1] for w in words[1:] if not w.startswith("-")]
        for target in targets:
            if is_outside(target, cwd):
                return f"this writes to {target}, outside the project ({PROJECT})."
    return None


def decide(call):
    """Decide whether a planned tool call may run.

    Args:
        call: The hook payload (``tool_name``, ``tool_input``, ``cwd``).

    Returns:
        A reason to block, or ``None`` to allow. Tools other than file writes and shells
        are always allowed.
    """
    tool, args = call.get("tool_name", ""), call.get("tool_input") or {}
    cwd = call.get("cwd") or str(PROJECT)
    if tool in FILE_TOOLS:
        target = args.get("file_path") or args.get("notebook_path") or ""
        if not target or is_outside(target, cwd):
            return f"{target or 'this file'} is outside the project ({PROJECT}). Temporary files go in .cache/."
    elif tool in SHELL_TOOLS:
        return check_shell(args.get("command", ""), cwd)
    return None


def main():
    """Read the payload from stdin, then exit 0 (allow) or 2 (block, reason on stderr)."""
    try:
        reason = decide(json.load(sys.stdin))
    except Exception as exc:      # fail closed: an unchecked call must not slip through
        reason = f"the guard hook failed ({type(exc).__name__}: {exc}), so the call was blocked to be safe."
    if reason:
        print(f"Blocked by .claude/hooks/guard_paths.py: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
