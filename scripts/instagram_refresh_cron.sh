#!/bin/bash
# Daily cron on Elastic Beanstalk: refresh/seed the long-lived Instagram access token.
# Canonical install path on EB: /opt/interoves/instagram_refresh.sh
# (content is embedded in .ebextensions/instagram_cron.config — keep in sync).
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/instagram_refresh.log
LOCK=/var/lock/instagram_refresh.lock

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
  echo "---- $(date -Is) instagram_refresh_token ----"
  python manage.py instagram_refresh_token
} >>"$LOG" 2>&1
