#!/bin/bash
# Download the Nutrimatic Wikipedia index from S3 into a deploy-stable cache.
# Runs after Daphne is up so a missing cache does not block the web process.

set -euo pipefail

APP=/var/app/current
PYTHON=$(ls /var/app/venv/*/bin/python 2>/dev/null | head -1)
CACHE="${NUTRIMATIC_INDEX_CACHE_DIR:-/var/app/nutrimatic_index_cache}"
LOG=/var/log/app/nutrimatic_index.log

mkdir -p /var/log/app "$CACHE"
if id webapp >/dev/null 2>&1; then
  chown -R webapp:webapp "$CACHE" 2>/dev/null || true
fi

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" | tee -a "$LOG"; }

if [[ -z "${PYTHON:-}" || ! -x "$PYTHON" ]]; then
  log "skip sync_nutrimatic_index: venv python not found"
  exit 0
fi

log "=== sync_nutrimatic_index start ==="
cd "$APP"
if "$PYTHON" manage.py sync_nutrimatic_index >>"$LOG" 2>&1; then
  log "=== sync_nutrimatic_index done ==="
else
  log "=== sync_nutrimatic_index FAILED (see $LOG) ==="
  exit 0
fi
