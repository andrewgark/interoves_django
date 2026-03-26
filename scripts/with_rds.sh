#!/usr/bin/env bash
# Tunnel RDS through the EB EC2 instance via SSM port forwarding, then run a
# Django management command (or any command) with prod credentials loaded.
#
# No security group changes — auth is purely IAM via the SSM session.
# Requires: aws CLI + session-manager-plugin, both on PATH.
#
# Usage (from repo root):
#   ./scripts/with_rds.sh manage.py check_background_migrations
#   ./scripts/with_rds.sh manage.py dbshell
#   ./scripts/with_rds.sh manage.py migrate --plan
#   ./scripts/with_rds.sh manage.py shell
#
# Pass --raw to run an arbitrary command instead of manage.py:
#   ./scripts/with_rds.sh --raw ./scripts/rds_mysql.sh -e "SHOW TABLES"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/../venv/interoves_django/bin/python"
ENV_FILE="${REPO_ROOT}/secrets/rds.env"
REGION="eu-central-1"
ENV_NAME="interoves-env"
TUNNEL_PORT=13306   # local port; avoids conflict with any local MySQL on 3306

# ---- Parse --raw flag -------------------------------------------------------
RAW=0
if [[ "${1:-}" == "--raw" ]]; then
    RAW=1
    shift
fi

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 [--raw] manage.py <command> [args...]" >&2
    exit 1
fi

# ---- Resolve running EB instance --------------------------------------------
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:elasticbeanstalk:environment-name,Values=${ENV_NAME}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)

echo "Tunnel: localhost:${TUNNEL_PORT} → ${INSTANCE_ID} → RDS:3306"

# ---- Load prod DB credentials -----------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
    set -a && source "$ENV_FILE" && set +a
else
    echo "Warning: $ENV_FILE not found; using current environment" >&2
fi

# ---- Start SSM port-forwarding tunnel ---------------------------------------
aws ssm start-session \
    --region "$REGION" \
    --target "$INSTANCE_ID" \
    --document-name "AWS-StartPortForwardingSessionToRemoteHost" \
    --parameters "{\"host\":[\"${RDS_HOSTNAME}\"],\"portNumber\":[\"3306\"],\"localPortNumber\":[\"${TUNNEL_PORT}\"]}" \
    > /tmp/ssm_tunnel.log 2>&1 &
TUNNEL_PID=$!

cleanup() {
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait until the tunnel is accepting connections (up to 15 s)
echo -n "Waiting for tunnel"
sleep 2   # SSM session setup takes ~2-3 s before the port opens
for i in $(seq 1 26); do
    if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',$TUNNEL_PORT))==0 else 1)" 2>/dev/null; then
        echo " ready"
        break
    fi
    echo -n "."
    sleep 0.5
    if [[ $i -eq 26 ]]; then
        echo " TIMEOUT" >&2
        echo "--- tunnel log ---" >&2 && cat /tmp/ssm_tunnel.log >&2
        exit 1
    fi
done

# ---- Point Django at the local tunnel ---------------------------------------
export RDS_HOSTNAME=127.0.0.1
export RDS_PORT=$TUNNEL_PORT

# ---- Run command -------------------------------------------------------------
cd "$REPO_ROOT"
if [[ $RAW -eq 1 ]]; then
    "$@"
else
    "$PYTHON" "$@"
fi
# trap cleanup EXIT fires here
