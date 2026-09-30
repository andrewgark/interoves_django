#!/usr/bin/env bash
# Avoid repeating collectstatic on an instance when all collected sources and
# the settings/dependencies that define collection are unchanged.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="/var/app/collectstatic-source.sha256"
cd "$APP_DIR"

CURRENT="$(scripts/static_source_fingerprint.sh)"
if [[ -f "$MARKER" && "$(<"$MARKER")" == "$CURRENT" ]]; then
  echo "collectstatic skipped: static sources unchanged ($CURRENT)"
  exit 0
fi

python3 manage.py collectstatic --noinput
printf '%s\n' "$CURRENT" > "${MARKER}.tmp"
mv "${MARKER}.tmp" "$MARKER"
echo "collectstatic complete; source fingerprint $CURRENT"
