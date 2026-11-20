#!/usr/bin/env bash
# Plan or execute the EB background -> ECS background handoff.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
AWS_PROFILE_NAME="${AWS_PROFILE:-default}"
IMAGE_URI="${1:-}"
APPLY=0
DRAIN_SECONDS=90

usage() {
    echo "Usage: $0 IMAGE_URI [--apply] [--drain-seconds N]" >&2
    exit 2
}
[[ -n "$IMAGE_URI" ]] || usage
shift
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --drain-seconds) DRAIN_SECONDS="${2:-}"; shift 2 ;;
        *) usage ;;
    esac
done
[[ "$DRAIN_SECONDS" =~ ^[0-9]+$ ]] || usage

aws_cmd() { AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" aws "$@"; }
QUEUE_URL="https://sqs.eu-central-1.amazonaws.com/916000456640/interoves-background"
EB_ENV=interoves-background-worker
ASG_NAME="$(aws_cmd elasticbeanstalk describe-environment-resources --environment-name "$EB_ENV" --query 'EnvironmentResources.AutoScalingGroups[0].Name' --output text)"
QUEUE_ATTRS="$(aws_cmd sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output json)"
VISIBLE="$(printf '%s' "$QUEUE_ATTRS" | ../venv/interoves_django/bin/python -c 'import json,sys; print(json.load(sys.stdin)["Attributes"].get("ApproximateNumberOfMessages","0"))')"
IN_FLIGHT="$(printf '%s' "$QUEUE_ATTRS" | ../venv/interoves_django/bin/python -c 'import json,sys; print(json.load(sys.stdin)["Attributes"].get("ApproximateNumberOfMessagesNotVisible","0"))')"
ASG_STATE="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'AutoScalingGroups[0].{min:MinSize,max:MaxSize,desired:DesiredCapacity,instances:length(Instances)}' --output json)"
ECS_STATE="$(aws_cmd ecs describe-services --cluster interoves-workers --services interoves-background-ecs --query 'services[0].{desired:desiredCount,running:runningCount}' --output json)"

echo "eb_environment=$EB_ENV asg=$ASG_NAME"
echo "queue_visible=$VISIBLE queue_in_flight=$IN_FLIGHT"
echo "eb_asg=$ASG_STATE ecs=$ECS_STATE"
echo "image=$IMAGE_URI drain_seconds=$DRAIN_SECONDS aws_profile=$AWS_PROFILE_NAME"
if [[ "$APPLY" != 1 ]]; then
    echo "plan_only=true (pass --apply to change AWS)"
    exit 0
fi
aws_cmd autoscaling update-auto-scaling-group \
    --auto-scaling-group-name "$ASG_NAME" \
    --min-size 0 --max-size 0 --desired-capacity 0

deadline=$((SECONDS + DRAIN_SECONDS))
while (( SECONDS < deadline )); do
    desired="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'AutoScalingGroups[0].DesiredCapacity' --output text)"
    instances="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'length(AutoScalingGroups[0].Instances)' --output text)"
    in_flight="$(aws_cmd sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text)"
    [[ "$desired" == 0 && "$instances" == 0 && "$in_flight" == 0 ]] && break
    sleep 10
done
desired="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'AutoScalingGroups[0].DesiredCapacity' --output text)"
instances="$(aws_cmd autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" --query 'length(AutoScalingGroups[0].Instances)' --output text)"
in_flight="$(aws_cmd sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text)"
[[ "$desired" == 0 && "$instances" == 0 && "$in_flight" == 0 ]] || { echo "EB ASG/queue did not drain before timeout." >&2; exit 1; }

"$ROOT/scripts/deploy_ecs_worker.sh" background "$IMAGE_URI" --profile normal --desired-count 1 --apply
echo "Background cutover complete: EB=0, ECS=1"
