#!/usr/bin/env bash
# Temporarily open RDS port 3306 for the current public IP, run a Django management
# command (or any command) with prod credentials loaded, then close the port on exit.
#
# Usage (from repo root):
#   ./scripts/with_rds.sh manage.py check_background_migrations
#   ./scripts/with_rds.sh manage.py dbshell
#   ./scripts/with_rds.sh manage.py migrate --plan
#   ./scripts/with_rds.sh manage.py shell
#
# The script wraps ./manage.py via the project venv automatically.
# Pass --raw to execute an arbitrary command instead:
#   ./scripts/with_rds.sh --raw ./scripts/rds_mysql.sh -e "SHOW TABLES"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/../venv/interoves_django/bin/python"
ENV_FILE="${REPO_ROOT}/secrets/rds.env"
RDS_SG="sg-0631c0b9e45b0f6b3"
REGION="eu-central-1"

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

# ---- Discover public IP -----------------------------------------------------
MY_IP=$(curl -s --max-time 5 https://checkip.amazonaws.com || curl -s --max-time 5 https://api4.my-ip.io/ip)
CIDR="${MY_IP}/32"

# ---- Open / close security group around the command -------------------------
cleanup() {
    echo "Closing RDS :3306 for ${CIDR} ..."
    aws ec2 revoke-security-group-ingress --region "$REGION" \
        --group-id "$RDS_SG" --protocol tcp --port 3306 --cidr "$CIDR" 2>/dev/null || true
}
trap cleanup EXIT

echo "Opening RDS :3306 for ${CIDR} ..."
aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$RDS_SG" --protocol tcp --port 3306 --cidr "$CIDR" 2>/dev/null \
    || echo "(rule may already exist — continuing)"

# ---- Load prod DB credentials -----------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
    set -a && source "$ENV_FILE" && set +a
else
    echo "Warning: $ENV_FILE not found; using current environment" >&2
fi

# ---- Run command -------------------------------------------------------------
cd "$REPO_ROOT"
if [[ $RAW -eq 1 ]]; then
    "$@"
else
    "$PYTHON" "$@"
fi
# trap cleanup EXIT fires here
