#!/bin/bash
# Wrapper script for bandit to handle argument parsing correctly
# Ignore all arguments passed by pre-commit (they are file paths)
#
# Fallback logic (per #522):
#   1. Prefer bare `bandit` binary on PATH
#   2. Fall back to the project venv's `python -m bandit`
#   3. Fail with a clear install message if neither works

if command -v bandit >/dev/null 2>&1; then
    bandit -ll -r . -c .bandit
else
    # Detect project venv python (relative to this script's repo root)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

    if [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -m bandit --version >/dev/null 2>&1; then
        "$VENV_PYTHON" -m bandit -ll -r . -c .bandit
    else
        echo "ERROR: bandit is not installed. Run: uv pip install -r requirements-dev.txt" >&2
        exit 1
    fi
fi
