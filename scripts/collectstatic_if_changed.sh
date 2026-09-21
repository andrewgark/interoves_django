#!/usr/bin/env bash
# Avoid repeating collectstatic on an instance when all collected sources and
# the settings/dependencies that define collection are unchanged.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="/var/app/collectstatic-source.sha256"
cd "$APP_DIR"

fingerprint() {
  # Django collects static files from the project-level static directory and
  # from every installed app. Include every app static directory so a change
  # outside games/ cannot incorrectly reuse a stale manifest.
  {
    find . -type d \( -name .git -o -name .venv -o -name venv -o -name __pycache__ \) -prune -o \
      -type d -name static -print0 |
      while IFS= read -r -d '' directory; do
        find "$directory" -type f -print0
      done | sort -zu | xargs -0 -r sha256sum
    sha256sum interoves_django/settings.py requirements.txt
  } | sha256sum | awk '{print $1}'
}

CURRENT="$(fingerprint)"
if [[ -f "$MARKER" && "$(<"$MARKER")" == "$CURRENT" ]]; then
  echo "collectstatic skipped: static sources unchanged ($CURRENT)"
  exit 0
fi

python3 manage.py collectstatic --noinput
printf '%s\n' "$CURRENT" > "${MARKER}.tmp"
mv "${MARKER}.tmp" "$MARKER"
echo "collectstatic complete; source fingerprint $CURRENT"
