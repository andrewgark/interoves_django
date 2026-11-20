#!/usr/bin/env bash
# Idempotent ASG schedules for interoves-env (Europe/Moscow).
# Do NOT put these in .ebextensions CloudFormation — CFN re-applies them on every
# stack update and can force mid-week capacity to Sunday peak (3).
#
# Sunday 17:00 MSK → min/max 3 (desired rises to min)
# Monday 00:00 MSK → min/max 2 (HA baseline; Max stays 2 until Sunday peak)
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

ASG=$(aws autoscaling describe-auto-scaling-groups --region "$REGION" \
  --query "AutoScalingGroups[?contains(AutoScalingGroupName, 'awseb-e-rkvpj3bv2a')].AutoScalingGroupName" \
  --output text | awk '{print $1}')
if [[ -z "$ASG" || "$ASG" == "None" ]]; then
  echo "ASG not found" >&2
  exit 1
fi
echo "ASG=$ASG"

# Delete CFN-named leftovers if present (after removing Resources from scaling.config)
for name in InterovesScheduleSundayPeak InterovesScheduleWeekBaseline; do
  aws autoscaling delete-scheduled-action --region "$REGION" \
    --auto-scaling-group-name "$ASG" --scheduled-action-name "$name" 2>/dev/null || true
done

# Replace legacy Monday→1 action if still present
aws autoscaling delete-scheduled-action --region "$REGION" \
  --auto-scaling-group-name "$ASG" --scheduled-action-name interoves-mon-1 2>/dev/null || true

aws autoscaling put-scheduled-update-group-action --region "$REGION" \
  --auto-scaling-group-name "$ASG" \
  --scheduled-action-name interoves-sun-3 \
  --recurrence '0 17 * * SUN' --time-zone Europe/Moscow \
  --min-size 3 --max-size 3

aws autoscaling put-scheduled-update-group-action --region "$REGION" \
  --auto-scaling-group-name "$ASG" \
  --scheduled-action-name interoves-mon-2 \
  --recurrence '0 0 * * MON' --time-zone Europe/Moscow \
  --min-size 2 --max-size 2

echo "Schedules upserted (no DesiredCapacity)."
aws autoscaling describe-scheduled-actions --region "$REGION" \
  --auto-scaling-group-name "$ASG" \
  --query 'ScheduledUpdateGroupActions[].[ScheduledActionName,MinSize,MaxSize,DesiredCapacity,Recurrence]' \
  --output table
