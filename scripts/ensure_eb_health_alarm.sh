#!/usr/bin/env bash
# Idempotent CloudWatch UnHealthyHostCount alarm for interoves-env.
# SNS topic is optional: interoves IAM often lacks SNS:CreateTopic — alarm still works in console.
#
# Usage:
#   ./scripts/ensure_eb_health_alarm.sh
#   ./scripts/ensure_eb_health_alarm.sh you@example.com   # try SNS subscribe if CreateTopic allowed
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

ALARM_NAME="interoves-elb-unhealthy-hosts"
TOPIC_NAME="interoves-eb-health-alerts"
EMAIL_SUB="${1:-}"
ACTIONS=()

TOPIC_ARN=""
if TOPIC_ARN=$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --query TopicArn --output text 2>/dev/null); then
  echo "SNS topic: $TOPIC_ARN"
  ACTIONS=(--alarm-actions "$TOPIC_ARN" --ok-actions "$TOPIC_ARN")
  if [[ -n "$EMAIL_SUB" ]]; then
    aws sns subscribe --region "$REGION" --topic-arn "$TOPIC_ARN" \
      --protocol email --notification-endpoint "$EMAIL_SUB" >/dev/null
    echo "Subscribed $EMAIL_SUB (confirm the email)."
  fi
else
  echo "SNS CreateTopic not permitted — creating alarm without notification actions."
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
TG_DIM="${TG_ARN#*:*:*:*:*:}"
LB_DIM="${LB_ARN#*:*:*:*:*:}"
LB_DIM="${LB_DIM#loadbalancer/}"

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
  "${ACTIONS[@]+"${ACTIONS[@]}"}"

echo "Alarm upserted: $ALARM_NAME"
