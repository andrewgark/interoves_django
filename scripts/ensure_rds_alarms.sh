#!/usr/bin/env bash
# Idempotent baseline alarms for the production RDS instance.
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

DB_IDENTIFIER="${RDS_INSTANCE_IDENTIFIER:-awseb-e-rkvpj3bv2a-stack-awsebrdsdatabase-xbcxyy6hynls}"
TOPIC_ARN="$(aws sns list-topics --region "$REGION" --query "Topics[?ends_with(TopicArn, ':interoves-eb-health-alerts')].TopicArn | [0]" --output text)"
ACTIONS=()
if [[ -n "$TOPIC_ARN" && "$TOPIC_ARN" != None ]]; then
  ACTIONS=(--alarm-actions "$TOPIC_ARN" --ok-actions "$TOPIC_ARN")
fi

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-rds-high-cpu \
  --alarm-description 'Production RDS CPU is consistently high' \
  --namespace AWS/RDS --metric-name CPUUtilization \
  --dimensions "Name=DBInstanceIdentifier,Value=${DB_IDENTIFIER}" \
  --statistic Average --period 300 --evaluation-periods 2 --datapoints-to-alarm 2 \
  --threshold 85 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching "${ACTIONS[@]+"${ACTIONS[@]}"}"

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-rds-high-connections \
  --alarm-description 'Production RDS connection count is near the small instance limit' \
  --namespace AWS/RDS --metric-name DatabaseConnections \
  --dimensions "Name=DBInstanceIdentifier,Value=${DB_IDENTIFIER}" \
  --statistic Maximum --period 300 --evaluation-periods 2 --datapoints-to-alarm 2 \
  --threshold 48 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching "${ACTIONS[@]+"${ACTIONS[@]}"}"

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name interoves-rds-low-memory \
  --alarm-description 'Production RDS freeable memory is critically low' \
  --namespace AWS/RDS --metric-name FreeableMemory \
  --dimensions "Name=DBInstanceIdentifier,Value=${DB_IDENTIFIER}" \
  --statistic Minimum --period 300 --evaluation-periods 2 --datapoints-to-alarm 2 \
  --threshold 67108864 --comparison-operator LessThanThreshold \
  --treat-missing-data notBreaching "${ACTIONS[@]+"${ACTIONS[@]}"}"

echo "RDS alarms upserted for ${DB_IDENTIFIER}."
