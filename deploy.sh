#!/usr/bin/env bash
# Optional: create use_aws_profile_default.sh in repo root to export AWS_PROFILE / credentials.
# Optional: set EB_BIN to full path to eb if it is not on PATH.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
if [[ "${BUNDLE_MICROSITES:-0}" == "1" ]]; then
  ./scripts/bundle_microsites.sh
else
  echo "Skipping microsite bundling (set BUNDLE_MICROSITES=1 to refresh local bundles)."
fi
if [[ ! -x nutrimatic_bundle/build/find-expr ]]; then
  echo "ERROR: nutrimatic_bundle/build/find-expr missing or not executable." >&2
  echo "Run scripts/bundle_microsites.sh (needs ~/nutrimatic-ru/build/find-expr) or restore the committed binary." >&2
  exit 1
fi
if [[ ! -f nutrimatic_bundle/cgi_scripts/cgi-search.py ]]; then
  echo "ERROR: nutrimatic_bundle/cgi_scripts/cgi-search.py missing." >&2
  exit 1
fi
./scripts/deploy_green.sh "${1:---deploy}"
