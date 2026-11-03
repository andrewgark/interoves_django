#!/usr/bin/env bash
# Same venv as run_server.sh — ../venv/interoves_django from repo root
set -euo pipefail
cd "$(dirname "$0")"
bash ./scripts/lint_new_ui_responsive.sh
bash ./scripts/lint_new_ui_tooltips.sh
bash ./scripts/lint_python.sh
PYTHON="../venv/interoves_django/bin/python3"
# The task group page script (static, plus its inline config) is not covered
# by any Django test.
if command -v node >/dev/null 2>&1; then
  "$PYTHON" scripts/check_inline_js_syntax.py static/templates/site/task_group.html
  node --check static/js/task_group_page.js
fi
TEST_SETTINGS="${DJANGO_TEST_SETTINGS:-interoves_django.test_settings}"
exec "$PYTHON" manage.py test --settings="$TEST_SETTINGS" --keepdb "$@"
