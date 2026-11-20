#!/bin/bash
# Periodically repair only missing/invalid daily result projections.
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/daily_result_projection_cron.log
LOCK=/var/lock/daily_result_projection_cron.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

{
  echo "---- $(date -Is) reconcile_daily_result_projections ----"
  python3 - "$APP_DIR" <<'PY'
import glob
import os
import subprocess
import sys

app_dir = sys.argv[1]
pid = subprocess.check_output(['pgrep', '-of', 'daphne'], text=True).strip()
env = os.environ.copy()
with open('/proc/{}/environ'.format(pid), 'rb') as source:
    for item in source.read().split(b'\0'):
        if b'=' in item:
            key, value = item.split(b'=', 1)
            env[key.decode()] = value.decode()
python = sorted(glob.glob('/var/app/venv/*/bin/python'))[-1]
subprocess.run(
    [python, 'manage.py', 'reconcile_daily_result_projections',
     '--apply', '--limit', '5'],
    cwd=app_dir, env=env, check=True,
)
PY
} >>"$LOG" 2>&1
