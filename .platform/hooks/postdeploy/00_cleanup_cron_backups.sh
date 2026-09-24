#!/usr/bin/env bash
# cfn-init can leave the previous content of an updated `files:` target as
# /etc/cron.d/<name>.bak.  cronie reads dotted filenames in this directory,
# so those backups become duplicate active jobs.  Keep cleanup narrowly
# scoped to our own cron namespace and run it after all EB files are installed.
set -euo pipefail

CRON_DIR=/etc/cron.d

find "$CRON_DIR" -maxdepth 1 -type f \( \
    -name 'interoves-*.bak' \
    -o -name 'interoves-*.backup' \
    -o -name 'interoves-*.old' \
    -o -name 'interoves-*.tmp' \
    -o -name 'interoves-*~' \
\) -print -exec rm -f -- {} +
