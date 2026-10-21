"""Filesystem confinement: every read and write in this project goes through this module.

Guardrails (CLAUDE.md):
    * Nothing outside the project directory is ever read or modified.
    * No file is ever deleted. Lessons go to fresh, never-reused run directories, and
      saves are atomic (write a temporary file, then rename over the target).
    * There is no ``import os`` anywhere in the project; ``pathlib`` covers everything needed.

Adapted from the spy-ief-strategy project, where this pattern was hardened, including the
Windows ``\\\\?\\`` prefix fix below.
"""

import json
import secrets
from datetime import datetime
from pathlib import Path

# The project root is the parent of the ``studysprint`` package directory.
PROJECT_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_DIR / "runs"
CACHE_DIR = PROJECT_DIR / ".cache"
STATIC_DIR = PROJECT_DIR / "studysprint" / "static"
FIXTURES_DIR = PROJECT_DIR / "studysprint" / "fixtures"


class ConfinementError(PermissionError):
    """Raised when code tries to touch a path outside the project directory."""


def confine(path):
    """Resolve ``path`` and refuse anything outside the project directory.

    Args:
        path: A ``str`` or ``Path``. Relative paths are taken from the project root,
            not the process's current directory, so behavior doesn't depend on where
            the app was launched from.

    Returns:
        The resolved absolute ``Path``, guaranteed to be inside ``PROJECT_DIR``.

    Raises:
        ConfinementError: If the resolved path is outside the project. ``..``
            traversal and symlinks are resolved before the check, so neither can
            escape it.
    """
    p = Path(path)
    resolved = _strip_extended_prefix((p if p.is_absolute() else PROJECT_DIR / p).resolve())
    if not resolved.is_relative_to(PROJECT_DIR):
        raise ConfinementError(f"Refusing to touch {resolved}: outside the project directory.")
    return resolved


def _strip_extended_prefix(path):
    """Normalize Windows extended-length paths (``\\\\?\\C:\\...``) to normal form.

    ``Path.resolve()`` can return the extended form, for example while another thread
    is creating a directory. ``is_relative_to`` would then reject a valid project path.
    UNC extended paths (``\\\\?\\UNC\\``) are left alone because they're never inside
    the project.

    Args:
        path: A resolved ``Path``.

    Returns:
        The same path without the ``\\\\?\\`` prefix, if it had one.
    """
    s = str(path)
    if s.startswith("\\\\?\\") and not s.startswith("\\\\?\\UNC\\"):
        return Path(s[4:])
    return path


def new_run_dir(label=None, root=RUNS_DIR):
    """Create a fresh, never-reused directory under ``runs/`` for one lesson build.

    Args:
        label: Optional short slug appended to the timestamp (e.g. ``"photosynthesis-g5"``).
        root: Parent directory (confined). Tests pass a scratch folder so they never
            clutter ``runs/``.

    Returns:
        The new directory's ``Path``.

    Raises:
        ConfinementError: If no free name is found after 1,000 attempts (practically
            impossible), or the label would escape ``runs/``.

    Side effects:
        Creates the directory, and ``runs/`` if needed.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{label}" if label else stamp
    for i in range(1000):
        candidate = confine(Path(root) / (base if i == 0 else f"{base}-{i}"))
        if not candidate.exists():
            # exist_ok=False makes concurrent builds that pick the same name fail
            # loudly here instead of silently sharing a directory.
            try:
                candidate.mkdir(parents=True)
            except FileExistsError:
                continue
            return candidate
    raise ConfinementError("Could not allocate a run directory.")


def write_text(path, text):
    """Atomically write UTF-8 text to a confined path.

    The text goes to ``<name>.<random>.tmp`` first, then replaces the target in one
    rename, so a crash never leaves a half-written file. The random suffix keeps
    concurrent writers from clobbering each other's temporary files.

    Args:
        path: Target path (confined).
        text: String content.

    Returns:
        The resolved target ``Path``.

    Raises:
        ConfinementError: If the path is outside the project.
    """
    path = confine(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def write_json(path, obj):
    """Atomically write ``obj`` as indented JSON. See :func:`write_text`.

    Args:
        path: Target path (confined).
        obj: Any JSON-serializable object. Datetimes are written as ISO strings.

    Returns:
        The resolved target ``Path``.
    """
    return write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, default=_json_default))


def read_json(path):
    """Read and parse a JSON file from a confined path.

    Args:
        path: Source path (confined).

    Returns:
        The parsed object.

    Raises:
        ConfinementError: If the path is outside the project.
        FileNotFoundError: If it doesn't exist.
        json.JSONDecodeError: If it isn't valid JSON.
    """
    return json.loads(confine(path).read_text(encoding="utf-8"))


def _json_default(o):
    """``json.dumps`` fallback: datetimes become ISO strings, and anything else is an error."""
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"Not JSON serializable: {type(o).__name__}")
