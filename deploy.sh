#!/usr/bin/env bash
# Optional: create use_aws_profile_default.sh in repo root to export AWS_PROFILE / credentials.
# Optional: set EB_BIN to full path to eb if it is not on PATH.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
./scripts/write_deploy_version.sh
if command -v ntpdate >/dev/null 2>&1; then
  # `sudo` may be non-interactive (e.g. when run from automation). Don't fail deploy on time sync.
  sudo -n ntpdate ntp.ubuntu.com || true
fi
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
