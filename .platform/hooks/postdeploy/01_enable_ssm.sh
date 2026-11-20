#!/bin/bash
set -euo pipefail

# Ensure the SSM agent is present and running so operational access does not
# depend on SSH.  Do not hide installation/start failures: a Worker without
# SSM is not safely operable during a controlled rollout.
if ! command -v amazon-ssm-agent >/dev/null 2>&1 && ! systemctl cat amazon-ssm-agent.service >/dev/null 2>&1; then
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)
            RPM_URL="https://s3.eu-central-1.amazonaws.com/amazon-ssm-eu-central-1/latest/linux_amd64/amazon-ssm-agent.rpm"
            ;;
        aarch64)
            RPM_URL="https://s3.eu-central-1.amazonaws.com/amazon-ssm-eu-central-1/latest/linux_arm64/amazon-ssm-agent.rpm"
            ;;
        *)
            echo "Unsupported architecture for amazon-ssm-agent: $ARCH" >&2
            exit 1
            ;;
    esac
    TMP_RPM="$(mktemp /var/tmp/amazon-ssm-agent.XXXXXX.rpm)"
    trap 'rm -f "$TMP_RPM"' EXIT
    curl --fail --silent --show-error --location "$RPM_URL" --output "$TMP_RPM"
    rpm --upgrade "$TMP_RPM"
fi

systemctl enable amazon-ssm-agent
systemctl restart amazon-ssm-agent
systemctl is-active --quiet amazon-ssm-agent
