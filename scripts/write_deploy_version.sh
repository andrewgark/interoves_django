#!/usr/bin/env bash
# Write current git short SHA into interoves_django/deploy_version.txt for EB bundles
# (when .git is not deployed on the instance). Run before `eb deploy` or in CI before zipping.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/interoves_django/deploy_version.txt"
SHA="$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "$SHA" ]]; then
  SHA="unknown"
fi
printf '%s\n' "$SHA" >"$OUT"
echo "Wrote SITE_DEPLOY_VERSION file: $SHA -> $OUT"
