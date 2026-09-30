#!/usr/bin/env bash
set -euo pipefail

# Keep the public origin stable when Elastic Beanstalk replaces the Green
# instance.  The EIP is intentionally specific to Green and is not used by
# Blue rollback or workers.
EIP_ALLOCATION_ID="eipalloc-04fce5f757cc4bedd"
INSTALL_PATH="/usr/local/sbin/interoves-associate-green-origin-eip"

# Keep a stable copy because /var/app/current is replaced during deployments.
# The systemd timer invokes the stable copy with --once; do not copy a file
# onto itself in that mode.
if [[ "${1:-}" != "--once" ]]; then
    install -D -m 0755 "$0" "$INSTALL_PATH"
fi

METADATA_TOKEN="$(curl -fsS -X PUT \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600' \
    http://169.254.169.254/latest/api/token)"
INSTANCE_ID="$(curl -fsS \
    -H "X-aws-ec2-metadata-token: ${METADATA_TOKEN}" \
    http://169.254.169.254/latest/meta-data/instance-id)"
REGION="$(curl -fsS \
    -H "X-aws-ec2-metadata-token: ${METADATA_TOKEN}" \
    http://169.254.169.254/latest/meta-data/placement/region)"

/usr/bin/aws ec2 associate-address \
    --region "${REGION}" \
    --allocation-id "${EIP_ALLOCATION_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --allow-reassociation >/dev/null

echo "Associated Green origin EIP ${EIP_ALLOCATION_ID} with ${INSTANCE_ID}"

cat >/etc/systemd/system/interoves-green-origin-eip.service <<'UNIT'
[Unit]
Description=Keep the Interoves Green origin EIP associated
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/interoves-associate-green-origin-eip --once
UNIT

cat >/etc/systemd/system/interoves-green-origin-eip.timer <<'UNIT'
[Unit]
Description=Reconcile the Interoves Green origin EIP

[Timer]
OnBootSec=10s
OnUnitActiveSec=10s
Unit=interoves-green-origin-eip.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now interoves-green-origin-eip.timer >/dev/null
