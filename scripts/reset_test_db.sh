#!/usr/bin/env bash
# Remove persistent SQLite test DB so the next ./run_tests.sh recreates schema from migrations.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -n "${INTEROVES_TEST_DB_PATH:-}" ]]; then
  DB_PATH="$INTEROVES_TEST_DB_PATH"
else
  DB_PATH="$(../venv/interoves_django/bin/python -c "import os, tempfile; print(os.environ.get('INTEROVES_TEST_DB_PATH') or os.path.join(tempfile.gettempdir(), 'interoves-django-test.sqlite3'))")"
fi

removed=0
for path in "$DB_PATH" "${DB_PATH}-journal" "${DB_PATH}-wal" "${DB_PATH}-shm"; do
  if [[ -f "$path" ]]; then
    rm -f "$path"
    echo "reset_test_db: removed $path"
    removed=1
  fi
done

if [[ "$removed" -eq 0 ]]; then
  echo "reset_test_db: nothing to remove at $DB_PATH"
else
  echo "reset_test_db: ok — run ./run_tests.sh (migrations will apply once)"
fi
