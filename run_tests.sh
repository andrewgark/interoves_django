#!/usr/bin/env bash
# Same venv as run_server.sh — ../venv/interoves_django from repo root
set -euo pipefail
cd "$(dirname "$0")"
bash ./scripts/lint_new_ui_responsive.sh
PYTHON="../venv/interoves_django/bin/python3"
# Inline <script> in task_group.html is not covered by any Django test.
if command -v node >/dev/null 2>&1; then
  "$PYTHON" scripts/check_inline_js_syntax.py static/templates/new/task_group.html
fi
exec "$PYTHON" manage.py test "$@"
