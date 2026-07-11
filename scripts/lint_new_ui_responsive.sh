#!/usr/bin/env bash
# Проверяет согласованность responsive-констант new UI (agents/AGENTS.md).
set -euo pipefail
cd "$(dirname "$0")/.."

CSS="static/css/new.css"
fail() { echo "lint_new_ui_responsive: $*" >&2; exit 1; }

[[ -f "$CSS" ]] || fail "missing $CSS"

break_wide="$(grep -E '^\s*--new-break-wide:\s*([0-9]+)px' "$CSS" | head -1 | sed -E 's/.*: *([0-9]+)px.*/\1/')"
[[ -n "$break_wide" ]] || fail "--new-break-wide not found in :root"
stack_max_px=$((break_wide - 1))

grep -qE "@media \(min-width: ${break_wide}px\)" "$CSS" \
  || fail ".new-wrap must use @media (min-width: ${break_wide}px) matching --new-break-wide"

grep -qE "@media \(max-width: ${stack_max_px}px\)" "$CSS" \
  || fail "raddle stack must use @media (max-width: ${stack_max_px}px) (= --new-break-wide - 1)"

raddle_chunk="$(sed -n '/─── Raddle/,/─── Тёмная тема/p' "$CSS")"
echo "$raddle_chunk" | grep -qE '@media \(max-width: 720px\)' \
  && fail "raddle section must not use @media (max-width: 720px); use ${stack_max_px}px"

echo "$raddle_chunk" | grep -q 'container-type: inline-size' \
  || fail ".new-raddle-task must set container-type: inline-size"

echo "$raddle_chunk" | grep -q '@container raddle-task' \
  || fail "raddle must have @container raddle-task stack rule"

echo "$raddle_chunk" | grep -q 'var(--raddle-clues-min)' \
  || fail "raddle grid must use minmax(var(--raddle-clues-min), 1fr)"

echo "$raddle_chunk" | grep -q 'min-width: 0' \
  || fail "raddle section must include min-width: 0 on shrinkable children"

echo "lint_new_ui_responsive: ok (--new-break-wide=${break_wide}px, stack<=${stack_max_px}px)"
