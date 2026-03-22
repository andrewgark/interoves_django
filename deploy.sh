#!/usr/bin/env bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
./scripts/write_deploy_version.sh
sudo ntpdate ntp.ubuntu.com
./use_aws_profile_default.sh
/home/andrewgark/.local/bin/eb deploy
