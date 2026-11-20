#!/usr/bin/env bash
# Plan or execute ECS background -> EB background rollback.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
AWS_PROFILE_NAME="${AWS_PROFILE:-default}"
IMAGE_URI="${1:-}"
APPLY=0
RESTORE_MIN=1
RESTORE_MAX=1

usage() {
    echo "Usage: $0 IMAGE_URI [--apply] [--restore-min N --restore-max N]" >&2
    exit 2
}
[[ -n "$IMAGE_URI" ]] || usage
shift
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --restore-min) RESTORE_MIN="${2:-}"; shift 2 ;;
        --restore-max) RESTORE_MAX="${2:-}"; shift 2 ;;
        *) usage ;;
    esac
done
[[ "$RESTORE_MIN" =~ ^[0-9]+$ && "$RESTORE_MAX" =~ ^[0-9]+$ ]] || usage

aws_cmd() { AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" aws "$@"; }
EB_ENV=interoves-background-worker
ASG_NAME="$(aws_cmd elasticbeanstalk describe-environment-resources --environment-name "$EB_ENV" --query 'EnvironmentResources.AutoScalingGroups[0].Name' --output text)"
ECS_STATE="$(aws_cmd ecs describe-services --cluster interoves-workers --services interoves-background-ecs --query 'services[0].{desired:desiredCount,running:runningCount}' --output json)"
ASG_STATE="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'AutoScalingGroups[0].{min:MinSize,max:MaxSize,desired:DesiredCapacity,instances:length(Instances)}' --output json)"

echo "eb_environment=$EB_ENV asg=$ASG_NAME"
echo "ecs=$ECS_STATE eb_asg=$ASG_STATE"
echo "image=$IMAGE_URI restore=${RESTORE_MIN}/${RESTORE_MAX} aws_profile=$AWS_PROFILE_NAME"
if [[ "$APPLY" != 1 ]]; then
    echo "plan_only=true (pass --apply to change AWS)"
    exit 0
fi

"$ROOT/scripts/deploy_ecs_worker.sh" background "$IMAGE_URI" --profile normal --desired-count 0 --apply
aws_cmd autoscaling update-auto-scaling-group \
    --auto-scaling-group-name "$ASG_NAME" \
    --min-size "$RESTORE_MIN" --max-size "$RESTORE_MAX" --desired-capacity "$RESTORE_MIN"
echo "Background rollback complete: ECS=0, EB=${RESTORE_MIN}/${RESTORE_MAX}"
