#!/usr/bin/env bash
# Не даёт возвращать нативные title-тултипы в новой UI.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "lint_new_ui_tooltips: $*" >&2; exit 1; }

paths=(static/templates/new static/templates/task-content)
violations="$(rg -n -P '(?<![\w.-])title\s*=' "${paths[@]}" --glob '*.html' 2>/dev/null | rg -v '\b(var|let|const) title\b|\.title\s*=' || true)"
if [[ -n "$violations" ]]; then
  printf '%s\n' "$violations" >&2
  fail "native title attributes found; use data-tooltip (audio-title is allowed for the audio player)"
fi

for script in static/js/daily_statistics.js static/js/support_word_salad.js; do
  [[ -f "$script" ]] || fail "missing $script"
  if violations="$(rg -n 'title\\s*=' "$script" 2>/dev/null)"; then
    printf '%s\n' "$violations" >&2
    fail "native title strings found in $script; use data-tooltip"
  fi
done

echo "lint_new_ui_tooltips: ok (native title disabled for new UI)"
