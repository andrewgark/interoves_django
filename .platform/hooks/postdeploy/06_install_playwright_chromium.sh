#!/usr/bin/env bash
set -euo pipefail

LOG=/var/log/app/playwright_chromium_install.log
mkdir -p /var/log/app

log() {
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" | tee -a "$LOG"
}

PYTHON="$(ls /var/app/venv/*/bin/python 2>/dev/null | head -1 || true)"
if [[ -z "$PYTHON" ]]; then
  log "skip: no EB Python venv found"
  exit 0
fi

ROLE="${INTEROVES_RUNTIME_ROLE:-web}"
if [[ "$ROLE" == "integration-worker" ]]; then
  APP_USER=app
  BROWSERS_PATH=/home/app/.cache/ms-playwright
  FONTS_PATH=/home/app/.fonts
elif [[ "$ROLE" == "web" || -z "$ROLE" ]]; then
  APP_USER=webapp
  BROWSERS_PATH=/home/webapp/.cache/ms-playwright
  FONTS_PATH=/home/webapp/.fonts
else
  log "skip: runtime role $ROLE does not render Playwright social images"
  exit 0
fi

mkdir -p "$BROWSERS_PATH" "$FONTS_PATH"
export PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_PATH"

log "installing Chromium for Playwright role=$ROLE path=$BROWSERS_PATH"
"$PYTHON" -m playwright install chromium 2>&1 | tee -a "$LOG"

cp -f /usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf "$FONTS_PATH/" 2>/dev/null \
  || cp -f /usr/share/fonts/noto/NotoColorEmoji.ttf "$FONTS_PATH/" 2>/dev/null \
  || log "NotoColorEmoji.ttf not found"

if id "$APP_USER" >/dev/null 2>&1; then
  chown -R "$APP_USER:$APP_USER" "$BROWSERS_PATH" "$FONTS_PATH"
fi

"$PYTHON" - <<'PY'
from pathlib import Path
import os

root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
matches = list(root.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))
if not matches:
    raise SystemExit("Playwright Chromium executable missing after install")
print("verified", matches[0])
PY

log "Chromium install verified"
