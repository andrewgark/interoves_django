#!/usr/bin/env bash
# Optional: create use_aws_profile_default.sh in repo root to export AWS_PROFILE / credentials.
# Optional: set EB_BIN to full path to eb if it is not on PATH.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
./scripts/bundle_microsites.sh
if [[ ! -x nutrimatic_bundle/build/find-expr ]]; then
  echo "ERROR: nutrimatic_bundle/build/find-expr missing or not executable." >&2
  echo "Run scripts/bundle_microsites.sh (needs ~/nutrimatic-ru/build/find-expr) or restore the committed binary." >&2
  exit 1
fi
if [[ ! -f nutrimatic_bundle/cgi_scripts/cgi-search.py ]]; then
  echo "ERROR: nutrimatic_bundle/cgi_scripts/cgi-search.py missing." >&2
  exit 1
fi
./scripts/write_deploy_version.sh
EB_BIN="${EB_BIN:-eb}"
eb_ok=0
if command -v "$EB_BIN" >/dev/null 2>&1; then
  eb_ok=1
elif [[ -x "$EB_BIN" ]]; then
  eb_ok=1
fi
if [[ "$eb_ok" -ne 1 ]]; then
  echo "Elastic Beanstalk CLI not found. Install 'eb' or set EB_BIN to its path." >&2
  exit 1
fi
# How long the EB CLI waits for the environment update (minutes). Long migrations may
# still run on AWS after this returns; use `eb status` / console events to confirm.
./scripts/aws_with_role.sh "$EB_BIN" deploy --timeout 15
echo "Deploy finished; smoking important pages…"
./scripts/smoke_prod_pages.sh
