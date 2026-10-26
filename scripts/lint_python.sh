#!/usr/bin/env bash
# Ruff on explicit paths, or on changed *.py vs origin/master when no args.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-../venv/interoves_django/bin/python}"
RUFF="${RUFF:-../venv/interoves_django/bin/ruff}"

if ! command -v "$RUFF" >/dev/null 2>&1; then
  echo "ruff not found at $RUFF — install: ../venv/interoves_django/bin/pip install ruff" >&2
  exit 1
fi

if [[ $# -gt 0 ]]; then
  exec "$RUFF" check "$@"
fi

mapfile -t CHANGED < <(
  {
    git diff --name-only origin/master -- '*.py' 2>/dev/null || true
    git diff --name-only HEAD -- '*.py' 2>/dev/null || true
    git ls-files --others --exclude-standard '*.py' 2>/dev/null || true
  } | sort -u | grep -v '^games/migrations/' || true
)
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "lint_python: no changed Python files vs origin/master; nothing to check"
  exit 0
fi
echo "lint_python: ruff check ${#CHANGED[@]} file(s)"
exec "$RUFF" check "${CHANGED[@]}"
