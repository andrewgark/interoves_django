#!/bin/bash
set -euo pipefail

AGENT_DIR=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d
case "${INTEROVES_RUNTIME_ROLE:-}" in
    worker)
        SOURCE=/var/app/current/infra/elasticbeanstalk/future/cloudwatch/amazon-cloudwatch-agent.json
        TARGET="$AGENT_DIR/file_interoves_worker.json"
        ENABLE_AGENT=0
        ;;
    web)
        SOURCE=/var/app/current/infra/elasticbeanstalk/green/cloudwatch/web-observability.json
        TARGET="$AGENT_DIR/file_interoves_web.json"
        ENABLE_AGENT=1
        ;;
    *)
        exit 0
        ;;
esac

if [[ ! -r "$SOURCE" ]]; then
    echo "CloudWatch Agent config missing: $SOURCE" >&2
    exit 1
fi

install -d -m 0755 "$AGENT_DIR"
install -o root -g root -m 0644 "$SOURCE" "$TARGET"
if [[ "$ENABLE_AGENT" == "1" ]]; then
    systemctl enable amazon-cloudwatch-agent.service
fi
systemctl restart amazon-cloudwatch-agent.service
