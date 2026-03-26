#!/usr/bin/env bash
# Run a command on the live EB instance using EC2 Instance Connect.
#
# Auth: IAM only — generates a throw-away SSH key, pushes the public half to
# the instance for 60 s via aws ec2-instance-connect, then SSHes in.
# No static key file, no security group changes, no SSM agent needed.
#
# Usage (from repo root):
#   ./scripts/eb_run.sh manage.py check_background_migrations
#   ./scripts/eb_run.sh manage.py migrate --plan
#   ./scripts/eb_run.sh manage.py shell
#   ./scripts/eb_run.sh --raw "cat /var/log/app/background_migrations.log"
#   ./scripts/eb_run.sh --raw "tail -f /var/log/web.stdout.log"

set -euo pipefail

REGION="eu-central-1"
ENV_NAME="interoves-env"
OS_USER="ec2-user"
APP_DIR="/var/app/current"
KEY_FILE="$(mktemp -u /tmp/eb_ic_XXXXXX)"

# ---- Parse flags ------------------------------------------------------------
RAW=0
if [[ "${1:-}" == "--raw" ]]; then
    RAW=1
    shift
fi

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 [--raw] manage.py <command> [args...]" >&2
    exit 1
fi

# ---- Resolve instance -------------------------------------------------------
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:elasticbeanstalk:environment-name,Values=${ENV_NAME}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
INSTANCE_IP=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo "Instance: ${INSTANCE_ID} (${INSTANCE_IP})"

# ---- Generate a throw-away key pair -----------------------------------------
cleanup() { rm -f "$KEY_FILE" "${KEY_FILE}.pub"; }
trap cleanup EXIT

ssh-keygen -t rsa -b 2048 -f "$KEY_FILE" -N "" -q

# ---- Push public key (valid for 60 s) ---------------------------------------
aws ec2-instance-connect send-ssh-public-key \
    --region "$REGION" \
    --instance-id "$INSTANCE_ID" \
    --instance-os-user "$OS_USER" \
    --ssh-public-key "file://${KEY_FILE}.pub" \
    --output text --query 'Success' | grep -q true \
    && echo "SSH key pushed (60 s window)" \
    || { echo "Failed to push SSH key" >&2; exit 1; }

# ---- Build remote command ---------------------------------------------------
if [[ $RAW -eq 1 ]]; then
    REMOTE_CMD="$*"
else
    # The venv path has a random suffix; resolve it with a glob on the instance.
    REMOTE_CMD="cd ${APP_DIR} && PYTHON=\$(ls /var/app/venv/*/bin/python | head -1) && \"\$PYTHON\" $*"
fi

# ---- SSH and run ------------------------------------------------------------
ssh -i "$KEY_FILE" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR \
    -o ConnectTimeout=10 \
    "${OS_USER}@${INSTANCE_IP}" "$REMOTE_CMD"
