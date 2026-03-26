#!/usr/bin/env bash
# Run a Django management command on the live EB instance via SSM Run Command.
# No SSH key, no security group change — just IAM.
#
# Usage (from repo root):
#   ./scripts/ssm_run.sh manage.py check_background_migrations
#   ./scripts/ssm_run.sh manage.py migrate --plan
#   ./scripts/ssm_run.sh manage.py shell_plus   # (blocks; interactive not supported)
#
# Pass --instance-id <id> to override auto-detection.
# Pass --raw to send an arbitrary shell command instead of a manage.py call:
#   ./scripts/ssm_run.sh --raw "journalctl -u web -n 50 --no-pager"
#   ./scripts/ssm_run.sh --raw "cat /var/log/app/background_migrations.log"

set -euo pipefail

REGION="eu-central-1"
ENV_NAME="interoves-env"
APP_DIR="/var/app/current"
VENV_PYTHON="/var/app/venv/staging-LQM1lest/bin/python"   # fallback; glob used on instance
TIMEOUT=120   # seconds to wait for command output

# ---- Parse flags ------------------------------------------------------------
INSTANCE_ID=""
RAW=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance-id) INSTANCE_ID="$2"; shift 2 ;;
        --raw)         RAW=1; shift ;;
        *)             break ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 [--instance-id <id>] [--raw] manage.py <command> [args...]" >&2
    exit 1
fi

# ---- Resolve instance -------------------------------------------------------
if [[ -z "$INSTANCE_ID" ]]; then
    INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
        --filters "Name=tag:elasticbeanstalk:environment-name,Values=${ENV_NAME}" \
                  "Name=instance-state-name,Values=running" \
        --query 'Reservations[0].Instances[0].InstanceId' --output text)
fi

if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
    echo "No running instance found for environment ${ENV_NAME}" >&2
    exit 1
fi
echo "Instance: ${INSTANCE_ID}"

# ---- Build shell command ----------------------------------------------------
if [[ $RAW -eq 1 ]]; then
    SHELL_CMD="$*"
else
    # Use glob to find the venv (suffix varies per deploy)
    SHELL_CMD="cd ${APP_DIR} && PYTHON=\$(ls /var/app/venv/*/bin/python | head -1) && \"\$PYTHON\" $*"
fi

# ---- Send SSM command -------------------------------------------------------
CMD_ID=$(aws ssm send-command --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "commands=[\"${SHELL_CMD}\"]" \
    --timeout-seconds "$TIMEOUT" \
    --query 'Command.CommandId' --output text)

echo "SSM command: ${CMD_ID}"
echo "Waiting for output..."

# ---- Poll for result --------------------------------------------------------
for i in $(seq 1 30); do
    sleep 4
    STATUS=$(aws ssm get-command-invocation --region "$REGION" \
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --query 'Status' --output text 2>/dev/null || echo "Pending")

    case "$STATUS" in
        Success)
            aws ssm get-command-invocation --region "$REGION" \
                --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
                --query 'StandardOutputContent' --output text
            exit 0
            ;;
        Failed|Cancelled|TimedOut)
            echo "--- STDOUT ---"
            aws ssm get-command-invocation --region "$REGION" \
                --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
                --query 'StandardOutputContent' --output text
            echo "--- STDERR ---"
            aws ssm get-command-invocation --region "$REGION" \
                --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
                --query 'StandardErrorContent' --output text
            echo "Command status: ${STATUS}" >&2
            exit 1
            ;;
        InProgress|Pending|Delayed)
            echo "  [${i}] ${STATUS}..."
            ;;
    esac
done

echo "Timed out waiting for SSM result" >&2
exit 1
