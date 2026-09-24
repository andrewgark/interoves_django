#!/bin/bash
set -euo pipefail

if [[ "${INTEROVES_RUNTIME_ROLE:-}" != "worker" ]]; then
    exit 0
fi

AGENT_DIR=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d
SOURCE=/var/app/current/infra/elasticbeanstalk/future/cloudwatch/amazon-cloudwatch-agent.json
TARGET="$AGENT_DIR/file_interoves_worker.json"

if [[ ! -r "$SOURCE" ]]; then
    echo "CloudWatch worker metrics config missing: $SOURCE" >&2
    exit 1
fi

install -d -m 0755 "$AGENT_DIR"
install -o root -g root -m 0644 "$SOURCE" "$TARGET"
systemctl restart amazon-cloudwatch-agent.service
