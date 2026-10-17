#!/bin/bash
set -euo pipefail

AGENT_DIR=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d
AGENT_CTL=/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl
case "${INTEROVES_RUNTIME_ROLE:-}" in
    worker)
        SOURCE=/var/app/current/infra/elasticbeanstalk/future/cloudwatch/amazon-cloudwatch-agent.json
        ENABLE_AGENT=0
        LEGACY_TARGET="$AGENT_DIR/file_interoves_worker.json"
        ;;
    web)
        SOURCE=/var/app/current/infra/elasticbeanstalk/green/cloudwatch/web-observability.json
        ENABLE_AGENT=1
        LEGACY_TARGET=""
        ;;
    *)
        exit 0
        ;;
esac

if [[ ! -r "$SOURCE" ]]; then
    echo "CloudWatch Agent config missing: $SOURCE" >&2
    exit 1
fi
if [[ ! -x "$AGENT_CTL" ]]; then
    echo "CloudWatch Agent control utility is missing: $AGENT_CTL" >&2
    exit 1
fi

if [[ -n "$LEGACY_TARGET" ]]; then
    rm -f "$LEGACY_TARGET"
fi
if [[ "$ENABLE_AGENT" == "1" ]]; then
    systemctl enable amazon-cloudwatch-agent.service
fi
"$AGENT_CTL" -a append-config -m ec2 -s -c "file:$SOURCE"
