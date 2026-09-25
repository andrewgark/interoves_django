#!/usr/bin/env bash
# Curl important public pages after deploy (or ad-hoc).
#
# Usage:
#   ./scripts/smoke_prod_pages.sh
#   SMOKE_BASE_URL=https://interoves.com ./scripts/smoke_prod_pages.sh
#
# List: scripts/smoke_prod_pages.list (paths only). Exit 1 if any response is >= 400.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIST="${REPO_ROOT}/scripts/smoke_prod_pages.list"
BASE_URL="${SMOKE_BASE_URL:-https://interoves.com}"
BASE_URL="${BASE_URL%/}"

if [[ ! -f "$LIST" ]]; then
  echo "Missing page list: $LIST" >&2
  exit 1
fi

fail=0
checked=0

while IFS= read -r line || [[ -n "$line" ]]; do
  # strip comments / whitespace
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  [[ "$line" == /* ]] || { echo "Skip (not a path): $line" >&2; continue; }

  url="${BASE_URL}${line}"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -L --max-redirs 5 --max-time 30 "$url" || echo "000")"
  checked=$((checked + 1))
  if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
    printf 'OK  %s  %s\n' "$code" "$line"
  else
    printf 'FAIL %s  %s\n' "$code" "$line" >&2
    fail=1
  fi
done < "$LIST"

echo "Checked ${checked} page(s) against ${BASE_URL}"
if [[ "$fail" -ne 0 ]]; then
  echo "smoke_prod_pages: failures above" >&2
  exit 1
fi
