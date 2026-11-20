#!/usr/bin/env bash
# Не даёт возвращать нативные title-тултипы в новой UI.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "lint_new_ui_tooltips: $*" >&2; exit 1; }

# Without ripgrep both checks used to pass silently; fall back to GNU grep -P.
search_html() {
  if command -v rg >/dev/null; then
    rg -n -P "$1" "${@:2}" --glob '*.html'
  else
    grep -rnP --include='*.html' "$1" "${@:2}"
  fi
}
search_file() {
  if command -v rg >/dev/null; then rg -n "$1" "$2"; else grep -nP "$1" "$2"; fi
}

paths=(static/templates/site static/templates/task-content)
violations="$(search_html '(?<![\w.-])title\s*=' "${paths[@]}" 2>/dev/null | grep -Ev '\b(var|let|const) title\b|\.title\s*=' || true)"
if [[ -n "$violations" ]]; then
  printf '%s\n' "$violations" >&2
  fail "native title attributes found; use data-tooltip (audio-title is allowed for the audio player)"
fi

for script in static/js/daily_statistics.js static/js/support_word_salad.js; do
  [[ -f "$script" ]] || fail "missing $script"
  if violations="$(search_file 'title\s*=' "$script" 2>/dev/null)"; then
    printf '%s\n' "$violations" >&2
    fail "native title strings found in $script; use data-tooltip"
  fi
done

# Page scripts moved out of templates keep the template rule: assigning
# .title from JS (or a local named title) is allowed there.
for script in static/js/task_group_page.js static/js/alphabetty_page.js; do
  [[ -f "$script" ]] || fail "missing $script"
  violations="$(search_file 'title\s*=' "$script" 2>/dev/null | grep -Ev '\b(var|let|const) title\b|\.title\s*=' || true)"
  if [[ -n "$violations" ]]; then
    printf '%s\n' "$violations" >&2
    fail "native title attributes found in $script; use data-tooltip"
  fi
done

echo "lint_new_ui_tooltips: ok (native title disabled for new UI)"
