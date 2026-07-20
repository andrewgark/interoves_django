#!/bin/bash
# Minute cron on Elastic Beanstalk: load env, run Telegram announcement tick
# (includes ladder channel schedule window at 00:15 MSK).
# Canonical install path on EB: /opt/interoves/telegram_cron.sh
# (content is embedded in .ebextensions/telegram_cron.config — keep in sync).
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/telegram_cron.log
LOCK=/var/lock/telegram_cron.lock

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
  echo "---- $(date -Is) telegram_game_announcements ----"
  python manage.py telegram_game_announcements
} >>"$LOG" 2>&1
