#!/usr/bin/env bash
# Web no longer runs Interoves cron. Workers never did. Remove anything a
# previous bundle left under /etc/cron.d and /opt/interoves, including .bak.
set -euo pipefail

case "${INTEROVES_RUNTIME_ROLE:-}" in
  web|background-worker|integration-worker) ;;
  *) exit 0 ;;
esac

rm -f /etc/cron.d/interoves-* /etc/cron.d/interoves-*.bak /opt/interoves/*cron*.sh
echo "${INTEROVES_RUNTIME_ROLE}: removed web cron files"
