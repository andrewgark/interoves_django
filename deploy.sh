#!/usr/bin/env bash
# Optional: create use_aws_profile_default.sh in repo root to export AWS_PROFILE / credentials.
# Optional: set EB_BIN to full path to eb if it is not on PATH.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
./scripts/write_deploy_version.sh
if command -v ntpdate >/dev/null 2>&1; then
  sudo ntpdate ntp.ubuntu.com
fi
if [[ -f "$REPO_ROOT/use_aws_profile_default.sh" ]]; then
  # shellcheck source=use_aws_profile_default.sh
  source "$REPO_ROOT/use_aws_profile_default.sh"
fi
EB_BIN="${EB_BIN:-eb}"
if ! command -v "$EB_BIN" >/dev/null 2>&1 && [[ ! -x "$EB_BIN" ]]; then
  echo "Elastic Beanstalk CLI not found. Install 'eb' or set EB_BIN to its path." >&2
  exit 1
fi
"$EB_BIN" deploy
