"""The Claude Code guard hook (.claude/hooks/guard_paths.py), run exactly as Claude Code runs it.

Each case pipes a PreToolUse payload to the script and checks the exit code: 0 allows the
tool call, 2 blocks it with a reason on stderr. "Outside" paths are built from the
project's parent folder so the cases are the same on Windows and Linux; Windows-only
spellings are skipped elsewhere (tasks/lessons.md: portable guardrail tests).
"""

import json
import subprocess
import sys

import pytest

from studysprint.paths import PROJECT_DIR

HOOK = PROJECT_DIR / ".claude" / "hooks" / "guard_paths.py"
OUTSIDE = PROJECT_DIR.parent / "outside-the-project.txt"
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")


def run_hook(payload, raw=None):
    """Run the hook with a payload on stdin. Returns (exit code, stderr)."""
    stdin = raw if raw is not None else json.dumps(payload)
    done = subprocess.run([sys.executable, "-B", str(HOOK)], input=stdin, capture_output=True,
                          text=True, timeout=30)
    return done.returncode, done.stderr


def shell(command, tool="Bash", cwd=PROJECT_DIR):
    """A shell-tool payload."""
    return {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(cwd)}


def write(path, tool="Write"):
    """A file-tool payload."""
    return {"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(PROJECT_DIR)}


@pytest.mark.parametrize("payload", [
    write(PROJECT_DIR / ".cache" / "notes.txt"),
    write(PROJECT_DIR / "tasks" / "todo.md", tool="Edit"),
    write("tasks/todo.md", tool="MultiEdit"),
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(PROJECT_DIR / "x.ipynb")}},
    {"tool_name": "Read", "tool_input": {"file_path": str(OUTSIDE)}},       # reads are out of scope
    shell("python -m pytest -q 2>&1 | tail -5"),
    shell("git status && git log --oneline -3"),
    shell("echo hello > .cache/out.txt && cat .cache/out.txt"),
    shell("ls missing 2>/dev/null; echo done"),
    shell("cp README.md .cache/readme-copy.md"),
    shell("mkdir -p .cache/sub && touch .cache/sub/x"),
    shell("echo rm is only a word here"),
    shell("echo up > ../tasks/x.md", cwd=PROJECT_DIR / ".cache"),      # resolves inside the project
    shell("Get-Process python | Select-Object Id", tool="PowerShell"),
    shell("'x' | Out-File .cache\\ps.txt", tool="PowerShell"),
], ids=lambda p: p.get("tool_input", {}).get("command") or p["tool_name"])
def test_allowed(payload):
    code, err = run_hook(payload)
    assert code == 0, err


@pytest.mark.parametrize("payload", [
    write(OUTSIDE),
    write(OUTSIDE, tool="Edit"),
    write("../outside-the-project.txt", tool="MultiEdit"),
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(OUTSIDE)}},
    shell("rm -rf build"),
    shell("sudo rm notes.txt"),
    shell("ls *.tmp | xargs rm"),
    shell("true & rm notes.txt"),
    shell("find . -name '*.tmp' -delete"),
    shell("git clean -fd"),
    shell("python -c \"import shutil; shutil." + "rmtree('build')\""),
    shell("echo hi > ../outside-the-project.txt"),
    shell(f"echo hi >> {OUTSIDE.as_posix()}"),
    shell("cp README.md ~/README.md"),
    shell("touch $HOME/notes.txt"),
    shell(f"mv notes.txt {OUTSIDE.as_posix()}"),
    shell("Remove-Item -Recurse .cache\\old", tool="PowerShell"),
    shell("Get-ChildItem .cache | Remove-Item", tool="PowerShell"),
    shell("'x' | Out-File $env:TEMP\\x.txt", tool="PowerShell"),
], ids=lambda p: p["tool_input"].get("command") or p["tool_name"] + " outside")
def test_blocked(payload):
    code, err = run_hook(payload)
    assert code == 2 and "Blocked by" in err


@WINDOWS
@pytest.mark.parametrize("command", ["echo x > /c/Windows/probe.txt",
                                     "echo x > C:/Users/Public/probe.txt",
                                     "Set-Content -Path C:\\Windows\\probe.txt -Value 1"])
def test_blocked_windows_spellings(command):
    code, _ = run_hook(shell(command))
    assert code == 2


@WINDOWS
def test_project_path_in_git_bash_spelling_allowed():
    """/c/Users/... is Git Bash for C:\\Users\\...; inside the project it must be allowed."""
    posix = PROJECT_DIR.as_posix()
    git_bash = f"/{posix[0].lower()}{posix[2:]}"
    code, err = run_hook(shell(f"echo x > {git_bash}/.cache/probe.txt"))
    assert code == 0, err


def test_fails_closed_on_bad_input():
    code, err = run_hook(None, raw="this is not json")
    assert code == 2 and "blocked to be safe" in err
