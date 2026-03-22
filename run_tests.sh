#!/usr/bin/env bash
# Same venv as run_server.sh — ../venv/interoves_django from repo root
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="../venv/interoves_django/bin/python3"
exec "$PYTHON" manage.py test "$@"
