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

{
  echo "---- $(date -Is) instagram_refresh_token ----"
  python3 - "$APP_DIR" <<'PY'
import glob
import os
import subprocess
import sys

app_dir = sys.argv[1]
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
subprocess.run(
    [python, 'manage.py', 'instagram_refresh_token'],
    cwd=app_dir,
    env=env,
    check=True,
)
PY
} >>"$LOG" 2>&1
