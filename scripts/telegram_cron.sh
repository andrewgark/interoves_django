#!/bin/bash
# Minute cron on Elastic Beanstalk: load env, run Telegram announcement tick
# (includes ladder channel schedule window at 00:15 MSK).
# Canonical install path on EB: /opt/interoves/telegram_cron.sh
# (content is embedded in .ebextensions/telegram_cron.config — keep in sync).
# Difficulty refresh is a separate cron (scripts/difficulty_cron.sh).
set -euo pipefail

APP_DIR=/var/app/current
LOG=/var/log/telegram_cron.log
LOCK=/var/lock/telegram_cron.lock

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

# EB environment secrets are present in Daphne's process environment, but are
# not returned by get-config environment. Copy that environment so cron can
# initialize Django and Telegram with the same production secrets.
eval "$(python3 -c '
import shlex, subprocess, sys
pid = subprocess.check_output(["pgrep", "-of", "daphne"], text=True).strip()
if not pid:
    sys.exit("Daphne process not found; cannot load EB environment")
with open("/proc/{}/environ".format(pid), "rb") as source:
    values = source.read().decode().split(chr(0))
for item in values:
    if "=" in item:
        key, value = item.split("=", 1)
        print("export {}={}".format(key, shlex.quote(value)))
')"

cd "$APP_DIR"
# shellcheck disable=SC1091
source /var/app/venv/*/bin/activate
# Cron runs as root; Playwright Chromium is installed for webapp (see .ebextensions/playwright.config).
# Without this, screenshot falls back to Pillow → broken Cyrillic / wrong styles in ladder posts.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/home/webapp/.cache/ms-playwright}"
{
  echo "---- $(date -Is) telegram_game_announcements ----"
  # A stuck Chromium/Telethon call must not hold the cron flock forever.
  timeout --foreground --signal=TERM --kill-after=15s 120s \
    python manage.py telegram_game_announcements
  echo "---- $(date -Is) telegram_admin_report ----"
  timeout --foreground --signal=TERM --kill-after=15s 90s \
    python manage.py telegram_admin_report
} >>"$LOG" 2>&1
