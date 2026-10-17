#!/bin/bash
set -euo pipefail

AGENT_DIR=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d
AGENT_CTL=/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl
configure_web_journal_forwarders() {
    install -d -m 0755 /var/log/interoves /etc/systemd/system

    cat > /etc/systemd/system/interoves-kernel-journal.service <<'EOF'
[Unit]
Description=Forward kernel journal messages to the CloudWatch Agent
After=systemd-journald.service
Wants=systemd-journald.service

[Service]
Type=simple
StateDirectory=interoves-journal-stream
ExecStart=/usr/bin/journalctl --follow --output=short-iso-precise --priority=info --cursor-file=/var/lib/interoves-journal-stream/kernel.cursor _TRANSPORT=kernel
StandardOutput=append:/var/log/interoves/kernel.log
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/interoves-systemd-journal.service <<'EOF'
[Unit]
Description=Forward systemd, systemd-oomd, and CloudWatch Agent journal messages
After=systemd-journald.service
Wants=systemd-journald.service

[Service]
Type=simple
StateDirectory=interoves-journal-stream
ExecStart=/usr/bin/journalctl --follow --output=short-iso-precise --priority=info --cursor-file=/var/lib/interoves-journal-stream/systemd.cursor _COMM=systemd + _SYSTEMD_UNIT=systemd-oomd.service + _SYSTEMD_UNIT=amazon-cloudwatch-agent.service
StandardOutput=append:/var/log/interoves/systemd.log
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/logrotate.d/interoves-journal-stream <<'EOF'
/var/log/interoves/kernel.log /var/log/interoves/systemd.log {
    size 10M
    rotate 3
    missingok
    notifempty
    compress
    copytruncate
}
EOF

    chmod 0644 /etc/systemd/system/interoves-kernel-journal.service \
        /etc/systemd/system/interoves-systemd-journal.service \
        /etc/logrotate.d/interoves-journal-stream
    systemctl daemon-reload
    systemctl enable --now interoves-kernel-journal.service interoves-systemd-journal.service
}

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
    configure_web_journal_forwarders
    systemctl enable amazon-cloudwatch-agent.service
fi
"$AGENT_CTL" -a append-config -m ec2 -s -c "file:$SOURCE"
