#!/usr/bin/env bash
# Run mysql against the EB-coupled RDS instance using the master password from Secrets Manager.
# Requires: aws CLI, mysql client, Python 3 (for JSON; jq optional).
# Network: RDS security group must allow this host on port 3306 (often: only EB in VPC — use SSM/bastion otherwise).
#
# Usage:
#   ./scripts/rds_mysql.sh -e "SELECT 1"
#   ./scripts/rds_mysql.sh ebdb < /tmp/script.sql
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SECRET_ARN="${RDS_SECRET_ARN:-arn:aws:secretsmanager:eu-central-1:916000456640:secret:rds!db-ce1a594a-9964-4a32-a9d3-9483ada5368c-0O6ead}"
RDS_HOST="${RDS_HOSTNAME:-awseb-e-rkvpj3bv2a-stack-awsebrdsdatabase-xbcxyy6hynls.cxq0z1jeedcf.eu-central-1.rds.amazonaws.com}"
RDS_PORT="${RDS_PORT:-3306}"
RDS_USER="${RDS_USERNAME:-admin}"
RDS_DB="${RDS_DB_NAME:-ebdb}"
SSL_CA="${MYSQL_SSL_CA:-$REPO_ROOT/secrets/global-bundle.pem}"

if [[ ! -f "$SSL_CA" ]]; then
  echo "Missing CA bundle: $SSL_CA (see secrets/README.md)" >&2
  exit 1
fi

_json="$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --region eu-central-1 --query SecretString --output text)"
export MYSQL_PWD="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$_json")"

exec mysql -h "$RDS_HOST" -P "$RDS_PORT" -u "$RDS_USER" \
  --ssl-mode=VERIFY_IDENTITY \
  --ssl-ca="$SSL_CA" \
  "$RDS_DB" "$@"
