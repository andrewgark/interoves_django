#!/bin/bash
# Hourly health-check entry on Elastic Beanstalk.
# The minute refresh is EventBridge schedule interoves-difficulty-refresh.
# Canonical install path on EB: /opt/interoves/difficulty_cron.sh
# (content is embedded in .ebextensions/difficulty_cron.config — keep in sync).
# flock only prevents two processes on the same instance; correctness is the DB claim.
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/difficulty_cron.log
if [[ "${1:-}" == "--health-check" ]]; then
  LOCK=/var/lock/difficulty_healthcheck.lock
else
  LOCK=/var/lock/difficulty_cron.lock
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

{
  echo "---- $(date -Is) refresh_daily_difficulty ----"
  python3 - "$APP_DIR" "${1:-}" <<'PY'
import glob
import os
import subprocess
import sys

app_dir, mode = sys.argv[1:]
try:
    pid = subprocess.check_output(['pgrep', '-of', 'daphne'], text=True).strip()
except subprocess.CalledProcessError as exc:
    raise SystemExit('Could not find the running Daphne process for EB environment') from exc

env = os.environ.copy()
with open('/proc/{}/environ'.format(pid), 'rb') as source:
    for item in source.read().split(b'\0'):
        if b'=' in item:
            key, value = item.split(b'=', 1)
            env[key.decode()] = value.decode()

python = sorted(glob.glob('/var/app/venv/*/bin/python'))[-1]
args = [python, 'manage.py']
if mode == '--health-check':
    args.append('check_daily_difficulty')
else:
    args.extend(['refresh_daily_difficulty', '--limit', '10'])
subprocess.run(args, cwd=app_dir, env=env, check=True)
PY
} >>"$LOG" 2>&1
