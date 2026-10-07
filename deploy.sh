#!/usr/bin/env bash
# Release the committed repository snapshot to the production Green ALB.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ "$#" -gt 1 ]]; then
  echo "Usage: $0 [--dry-run|--deploy]" >&2
  exit 2
fi
DEPLOY_MODE="${1:---deploy}"
case "$DEPLOY_MODE" in
  --dry-run|--deploy) ;;
  *) echo "Usage: $0 [--dry-run|--deploy]" >&2; exit 2 ;;
esac

SOURCE_SHA="$(git rev-parse HEAD)"
SOURCE_SHORT_SHA="$(git rev-parse --short HEAD)"
SOURCE_DIR="$(mktemp -d /tmp/interoves-deploy-source.XXXXXX)"
trap 'rm -rf "$SOURCE_DIR"' EXIT

# Never let unrelated or uncommitted checkout files leak into a production zip.
git archive "$SOURCE_SHA" | tar -x -C "$SOURCE_DIR"

# Local-only settings are needed for preflight/collectstatic but are excluded
# from EB bundles by .ebignore. Link only the one required Django secret.
if [[ -f "$REPO_ROOT/secrets/django_secret_key.txt" ]]; then
  ln -s "$REPO_ROOT/secrets/django_secret_key.txt" "$SOURCE_DIR/secrets/django_secret_key.txt"
fi

export DEPLOY_SOURCE_DIR="$SOURCE_DIR"
export DEPLOY_SOURCE_SHA="$SOURCE_SHORT_SHA"
export PYTHON="${PYTHON:-$REPO_ROOT/../venv/interoves_django/bin/python}"

if [[ "${BUNDLE_MICROSITES:-0}" == "1" ]]; then
  "$SOURCE_DIR/scripts/bundle_microsites.sh"
else
  echo "Skipping microsite bundling (set BUNDLE_MICROSITES=1 to refresh local bundles)."
fi
if [[ ! -x "$SOURCE_DIR/nutrimatic_bundle/build/find-expr" ]]; then
  echo "ERROR: nutrimatic_bundle/build/find-expr missing or not executable." >&2
  echo "Run scripts/bundle_microsites.sh (needs ~/nutrimatic-ru/build/find-expr) or restore the committed binary." >&2
  exit 1
fi
if [[ ! -f "$SOURCE_DIR/nutrimatic_bundle/cgi_scripts/cgi-search.py" ]]; then
  echo "ERROR: nutrimatic_bundle/cgi_scripts/cgi-search.py missing." >&2
  exit 1
fi
echo "Release source: $SOURCE_SHA (committed snapshot; working-tree edits excluded)."
"$REPO_ROOT/scripts/deploy_green.sh" "$DEPLOY_MODE"
