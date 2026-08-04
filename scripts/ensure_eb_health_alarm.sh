#!/usr/bin/env bash
# Idempotent: SNS topic + CloudWatch UnHealthyHostCount alarm for interoves-env.
# EB CFN cannot create SNS (service role). Run with AWS_PROFILE=interoves (or after bootstrap).
#
# Usage:
#   ./scripts/ensure_eb_health_alarm.sh
#   ./scripts/ensure_eb_health_alarm.sh you@example.com   # also email-subscribe
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/interoves_aws_bootstrap.sh"
# Prefer interoves user for SNS/CW; bootstrap may assume ai-bot which lacks SNS.
export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
TOPIC_NAME="interoves-eb-health-alerts"
ALARM_NAME="interoves-elb-unhealthy-hosts"
EMAIL_SUB="${1:-}"

TOPIC_ARN=$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --query TopicArn --output text)
echo "SNS topic: $TOPIC_ARN"

if [[ -n "$EMAIL_SUB" ]]; then
  aws sns subscribe --region "$REGION" --topic-arn "$TOPIC_ARN" \
    --protocol email --notification-endpoint "$EMAIL_SUB" >/dev/null
  echo "Subscribed $EMAIL_SUB (confirm the email)."
fi

TG_ARN=$(aws elbv2 describe-target-groups --region "$REGION" \
  --query "TargetGroups[?contains(TargetGroupName, 'AWSEB')].TargetGroupArn" --output text \
  | awk '{print $1}')
if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
  echo "No AWSEB target group found in $REGION" >&2
  exit 1
fi
LB_ARN=$(aws elbv2 describe-target-groups --region "$REGION" --target-group-arns "$TG_ARN" \
  --query 'TargetGroups[0].LoadBalancerArns[0]' --output text)
TG_DIM="${TG_ARN#*:*:*:*:*:}"          # targetgroup/name/id
LB_DIM="${LB_ARN#*:*:*:*:*:}"          # loadbalancer/app/name/id
LB_DIM="${LB_DIM#loadbalancer/}"       # app/name/id

echo "TargetGroup dim: $TG_DIM"
echo "LoadBalancer dim: $LB_DIM"

aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "$ALARM_NAME" \
  --alarm-description "ALB target unhealthy; ASG ELB health should replace the instance" \
  --namespace AWS/ApplicationELB \
  --metric-name UnHealthyHostCount \
  --statistic Maximum \
  --period 60 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --dimensions "Name=TargetGroup,Value=${TG_DIM}" "Name=LoadBalancer,Value=${LB_DIM}" \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN"

echo "Alarm upserted: $ALARM_NAME"
