#!/usr/bin/env sh
# Study Sprint launcher (macOS/Linux). Same commands as run.ps1; see that file for the list.
# Caches are kept inside the project by setting these before Python starts (CLAUDE.md).
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPYCACHEPREFIX="$ROOT/.cache/pycache"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.cache/ms-playwright"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"
cmd="$1"; [ $# -gt 0 ] && shift
case "$cmd" in
  test) exec "$PY" -m pytest "$@" ;;
  eval) exec "$PY" -m evals.run_evals "$@" ;;
  install-browser) exec "$PY" -m playwright install chromium ;;
  *) exec "$PY" -m studysprint "$cmd" "$@" ;;
esac
