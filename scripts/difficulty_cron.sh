#!/bin/bash
# Minute cron on Elastic Beanstalk: refresh due daily-game difficulty snapshots.
# Canonical install path on EB: /opt/interoves/difficulty_cron.sh
# (content is embedded in .ebextensions/difficulty_cron.config — keep in sync).
# flock only prevents two processes on the same instance; correctness is the DB claim.
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/difficulty_cron.log
LOCK=/var/lock/difficulty_cron.lock

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

eval "$(/opt/elasticbeanstalk/bin/get-config environment | python3 -c '
import json, shlex, sys
env = json.load(sys.stdin)
for key, value in env.items():
    print("export {}={}".format(key, shlex.quote(str(value))))
')"

cd "$APP_DIR"
# shellcheck disable=SC1091
source /var/app/venv/*/bin/activate
{
  echo "---- $(date -Is) refresh_daily_difficulty ----"
  python manage.py refresh_daily_difficulty --limit 10
} >>"$LOG" 2>&1
