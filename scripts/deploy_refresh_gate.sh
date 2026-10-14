#!/usr/bin/env bash
# Toggle the browser deploy-refresh gate in the shared production Redis cache.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"
case "$MODE" in
  enable)
    CODE="from django.conf import settings; from django.core.cache import caches; c=caches['track_revisions']; assert 'redis' in settings.CACHES['track_revisions']['BACKEND'].lower(), 'shared Redis cache is required'; c.set('site:deploy-refresh-gate', '1', timeout=3600); assert c.get('site:deploy-refresh-gate') == '1', 'failed to verify deploy refresh gate'"
    ;;
  disable)
    CODE="from django.core.cache import caches; caches['track_revisions'].delete('site:deploy-refresh-gate')"
    ;;
  *)
    echo "Usage: $0 enable|disable" >&2
    exit 2
    ;;
esac

"$ROOT/scripts/eb_run.sh" manage.py shell -c "$CODE"
