"""CLAUDE.md guardrails, enforced by tests: confinement, no `os`, no deletions, caches inside the project."""

import re
from pathlib import Path

import pytest

from studysprint.paths import PROJECT_DIR, ConfinementError, confine, new_run_dir, write_json, read_json

# Every Python file we wrote (not the venv or caches).
SOURCES = [p for p in PROJECT_DIR.rglob("*.py")
           if not any(part in {".venv", ".cache", ".pytest_tmp", "build"} for part in p.parts)]


def test_sources_found():
    """Sanity check: the scans below would pass vacuously if no files were found."""
    assert len(SOURCES) > 15


@pytest.mark.parametrize("path", ["..\\evil.txt", "../evil.txt", "runs/../../evil.txt", "C:\\Windows\\win.ini", "/etc/passwd"])
def test_confine_rejects_outside_paths(path):
    with pytest.raises(ConfinementError):
        confine(path)


def test_confine_accepts_project_paths_and_extended_prefix():
    assert confine("runs/x") == PROJECT_DIR / "runs" / "x"
    assert confine("\\\\?\\" + str(PROJECT_DIR / "docs")) == PROJECT_DIR / "docs"


def test_no_os_import_anywhere():
    """CLAUDE.md: never use `import os` (including `from os import ...` and `import os.path`)."""
    pattern = re.compile(r"^\s*(import\s+os\b|from\s+os\b)", re.MULTILINE)
    offenders = [str(p.relative_to(PROJECT_DIR)) for p in SOURCES if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_no_file_deletion_calls():
    """CLAUDE.md: never delete files. No unlink/rmdir/rmtree/remove anywhere in our code."""
    pattern = re.compile(r"\.(unlink|rmdir)\(|shutil\.(rmtree|move)|\bremove\(\s*['\"/]")
    this = Path(__file__).resolve()
    offenders = [str(p.relative_to(PROJECT_DIR)) for p in SOURCES
                 if p.resolve() != this and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_run_dirs_are_fresh_and_writes_atomic(workdir):
    a, b = new_run_dir("x", root=workdir), new_run_dir("x", root=workdir)
    assert a != b and a.parent == b.parent == workdir
    target = write_json(a / "f.json", {"ok": 1})
    assert read_json(target) == {"ok": 1}
    assert not list(a.glob("*.tmp"))        # the temporary file was renamed over the target


def test_launchers_keep_caches_inside_project():
    """Caches must be redirected into .cache/ before Python starts (Python code can't, without `os`)."""
    for launcher in ("run.ps1", "run.sh"):
        text = (PROJECT_DIR / launcher).read_text(encoding="utf-8")
        assert "PYTHONPYCACHEPREFIX" in text and ".cache" in text
        assert "PLAYWRIGHT_BROWSERS_PATH" in text and "ms-playwright" in text


def test_server_binds_to_loopback_only():
    from studysprint.cli import HOST
    assert HOST == "127.0.0.1"


def test_every_module_class_and_function_has_a_docstring():
    """CLAUDE.md: docstrings on every module, class and function (nested tool handlers included)."""
    import ast
    missing = []
    for path in SOURCES:
        rel = path.relative_to(PROJECT_DIR)
        if rel.parts[0] not in ("studysprint", "evals"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            missing.append(f"{rel} (module)")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(node):
                missing.append(f"{rel}:{node.lineno} {node.name}")
    assert missing == []
