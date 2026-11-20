#!/bin/bash
# Download the DB-IP country database (site-version hint) into a deploy-stable
# directory. Skips when the file is under 25 days old; a failure only means
# no hint is shown, so it never fails the deploy.

set -uo pipefail

APP=/var/app/current
PYTHON=$(ls /var/app/venv/*/bin/python 2>/dev/null | head -1)
DIR=/var/app/geoip
LOG=/var/log/app/geoip_sync.log

mkdir -p /var/log/app "$DIR"
if id webapp >/dev/null 2>&1; then
  chown webapp:webapp "$DIR" 2>/dev/null || true
fi

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" | tee -a "$LOG"; }

if [[ -z "${PYTHON:-}" || ! -x "$PYTHON" ]]; then
  log "skip sync_geoip_db: venv python not found"
  exit 0
fi

cd "$APP"
if GEOIP_COUNTRY_DB="$DIR/dbip-country-lite.mmdb" "$PYTHON" manage.py sync_geoip_db >>"$LOG" 2>&1; then
  log "sync_geoip_db done"
else
  log "sync_geoip_db FAILED (see $LOG)"
fi
exit 0
