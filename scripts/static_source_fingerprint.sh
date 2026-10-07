#!/usr/bin/env bash
# Print the content fingerprint used to decide whether collectstatic is needed.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

{
    find . -type d \( -name .git -o -name .venv -o -name venv -o -name __pycache__ \) -prune -o \
        -type d -name static -print0 |
        while IFS= read -r -d '' directory; do
            # Booklet files are served by microsites views and fetched lazily;
            # templates are loaded by Django, not served as static files. Do
            # not hash either tree on every deploy.
            find "$directory" \
                -path '*/microsites/eurovision_booklet' -prune -o \
                -path '*/templates' -prune -o \
                -type f -print0
        done | sort -zu | xargs -0 -r sha256sum
    sha256sum interoves_django/settings.py requirements.txt
} | sha256sum | awk '{print $1}'
