#!/usr/bin/env bash
# Idempotently provision Green web CloudWatch log groups, IAM and alarms.
# Default is read-only plan. Pass --apply only during an approved AWS change window.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
ENV_NAME="interoves-web-green-lb"
ROLE_NAME="interoves-green-web-role"
POLICY_NAME="interoves-green-web-observability"
SNS_TOPIC_NAME="interoves-eb-health-alerts"
SYSTEMD_LOG_GROUP="/aws/elasticbeanstalk/${ENV_NAME}/systemd"
KERNEL_LOG_GROUP="/aws/elasticbeanstalk/${ENV_NAME}/kernel"
POLICY_FILE="$ROOT/infra/elasticbeanstalk/green/cloudwatch/web-instance-observability-policy.json"
AWS_WRAPPER="$ROOT/scripts/aws_with_role.sh"
AWS_PROFILE_OVERRIDE="${OBSERVABILITY_AWS_PROFILE:-}"
ACTION="${1:---plan}"

if [[ $# -gt 1 || ( "$ACTION" != "--plan" && "$ACTION" != "--apply" ) ]]; then
    echo "Usage: $0 [--plan|--apply]" >&2
    exit 2
fi

aws_prod() {
    if [[ -n "$AWS_PROFILE_OVERRIDE" ]]; then
        aws --profile "$AWS_PROFILE_OVERRIDE" "$@"
    else
        "$AWS_WRAPPER" aws "$@"
    fi
}

LB_ARN="$(aws_prod elasticbeanstalk describe-environment-resources \
    --region "$REGION" --environment-name "$ENV_NAME" \
    --query 'EnvironmentResources.LoadBalancers[0].Name' --output text)"
TG_ARN="$(aws_prod elbv2 describe-target-groups --region "$REGION" \
    --load-balancer-arn "$LB_ARN" \
    --query 'TargetGroups[0].TargetGroupArn' --output text)"
ASG_NAME="$(aws_prod elasticbeanstalk describe-environment-resources \
    --region "$REGION" --environment-name "$ENV_NAME" \
    --query 'EnvironmentResources.AutoScalingGroups[0].Name' --output text)"
SNS_TOPIC_ARN="$(aws_prod sns list-topics --region "$REGION" \
    --query "Topics[?ends_with(TopicArn, ':${SNS_TOPIC_NAME}')].TopicArn | [0]" --output text)"

for value in "$LB_ARN" "$TG_ARN" "$ASG_NAME" "$SNS_TOPIC_ARN"; do
    if [[ -z "$value" || "$value" == "None" ]]; then
        echo "Could not resolve a required Green resource or existing SNS topic." >&2
        exit 1
    fi
done

LB_DIM="${LB_ARN#*loadbalancer/}"
TG_DIM="targetgroup/${TG_ARN#*targetgroup/}"

echo "Environment: $ENV_NAME"
echo "Load balancer: $LB_DIM"
echo "Target group: $TG_DIM"
echo "Auto Scaling group: $ASG_NAME"
echo "Alarm topic: $SNS_TOPIC_ARN"
echo "Log groups: $SYSTEMD_LOG_GROUP, $KERNEL_LOG_GROUP (14 days)"
echo "IAM role policy: $ROLE_NAME/$POLICY_NAME"
echo "Existing Green alarms will be updated in place; ELB 502/504, resource, OOM and Daphne failure alarms will be added."

if [[ "$ACTION" == "--plan" ]]; then
    echo "Plan only. Re-run with --apply to make these AWS changes."
    exit 0
fi

ensure_log_group() {
    local group_name="$1"
    if ! aws_prod logs describe-log-groups --region "$REGION" \
        --log-group-name-prefix "$group_name" \
        --query "logGroups[?logGroupName=='${group_name}'].logGroupName | [0]" \
        --output text | grep -Fxq "$group_name"; then
        aws_prod logs create-log-group --region "$REGION" --log-group-name "$group_name"
    fi
    aws_prod logs put-retention-policy --region "$REGION" \
        --log-group-name "$group_name" --retention-in-days 14
}

ensure_alarm() {
    local alarm_name="$1" description="$2" namespace="$3" metric="$4"
    local statistic="$5" period="$6" evaluation_periods="$7"
    local datapoints="$8" threshold="$9" comparison="${10}"
    local missing="${11}" dimensions_json="${12}"
    local dimension_args=()
    while IFS=$'\t' read -r dim_name dim_value; do
        [[ -z "$dim_name" ]] || dimension_args+=("Name=${dim_name},Value=${dim_value}")
    done < <(python3 -c \
        'import json,sys; [print("{}\t{}".format(d["Name"], d["Value"])) for d in json.loads(sys.argv[1])]' \
        "$dimensions_json")

    local alarm_args=(cloudwatch put-metric-alarm --region "$REGION" \
        --alarm-name "$alarm_name" --alarm-description "$description" \
        --namespace "$namespace" --metric-name "$metric" \
        --statistic "$statistic" --period "$period" \
        --evaluation-periods "$evaluation_periods" \
        --datapoints-to-alarm "$datapoints" --threshold "$threshold" \
        --comparison-operator "$comparison" --treat-missing-data "$missing")
    if (( ${#dimension_args[@]} )); then
        alarm_args+=(--dimensions "${dimension_args[@]}")
    fi
    alarm_args+=(--alarm-actions "$SNS_TOPIC_ARN" --ok-actions "$SNS_TOPIC_ARN")
    aws_prod "${alarm_args[@]}"
}

ensure_log_group "$SYSTEMD_LOG_GROUP"
ensure_log_group "$KERNEL_LOG_GROUP"
aws_prod iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "$POLICY_NAME" --policy-document "file://$POLICY_FILE"

aws_prod logs put-metric-filter --region "$REGION" \
    --log-group-name "$KERNEL_LOG_GROUP" \
    --filter-name interoves-green-kernel-oom \
    --filter-pattern '?"Out of memory" ?"oom-kill" ?"Killed process"' \
    --metric-transformations metricName=KernelOomEvents,metricNamespace=InterOves/Web,metricValue=1
aws_prod logs put-metric-filter --region "$REGION" \
    --log-group-name "$SYSTEMD_LOG_GROUP" \
    --filter-name interoves-green-web-process-failure \
    --filter-pattern '?"web.service: Main process exited" ?"web.service: Failed with result"' \
    --metric-transformations metricName=WebServiceFailures,metricNamespace=InterOves/Web,metricValue=1

elb_dimensions="[{\"Name\":\"LoadBalancer\",\"Value\":\"$LB_DIM\"}]"
target_dimensions="[{\"Name\":\"LoadBalancer\",\"Value\":\"$LB_DIM\"},{\"Name\":\"TargetGroup\",\"Value\":\"$TG_DIM\"}]"
asg_dimensions="[{\"Name\":\"AutoScalingGroupName\",\"Value\":\"$ASG_NAME\"}]"
no_dimensions='[]'

ensure_alarm interoves-elb-target-5xx \
    "Green target-generated 5xx responses" AWS/ApplicationELB \
    HTTPCode_Target_5XX_Count Sum 300 1 1 3 GreaterThanOrEqualToThreshold notBreaching "$target_dimensions"
ensure_alarm interoves-elb-unhealthy-hosts \
    "A target in Green is unhealthy" AWS/ApplicationELB \
    UnHealthyHostCount Maximum 60 5 3 1 GreaterThanOrEqualToThreshold notBreaching "$target_dimensions"
ensure_alarm interoves-elb-no-healthy-hosts \
    "Green ALB has no healthy targets" AWS/ApplicationELB \
    HealthyHostCount Minimum 60 3 3 1 LessThanThreshold notBreaching "$target_dimensions"
ensure_alarm interoves-green-alb-generated-502 \
    "Green ALB generated at least three HTTP 502 responses in five minutes" AWS/ApplicationELB \
    HTTPCode_ELB_502_Count Sum 300 1 1 3 GreaterThanOrEqualToThreshold notBreaching "$elb_dimensions"
ensure_alarm interoves-green-alb-generated-504 \
    "Green ALB generated at least three HTTP 504 responses in five minutes" AWS/ApplicationELB \
    HTTPCode_ELB_504_Count Sum 300 1 1 3 GreaterThanOrEqualToThreshold notBreaching "$elb_dimensions"
ensure_alarm interoves-green-web-high-cpu \
    "Green web CPU stayed above 90 percent for three of five one-minute periods" InterOves/Web \
    cpu_usage_active Average 60 5 3 90 GreaterThanOrEqualToThreshold notBreaching "$asg_dimensions"
ensure_alarm interoves-green-web-high-memory \
    "Green web host memory utilization is at least 85 percent" InterOves/Web \
    mem_used_percent Maximum 60 3 2 85 GreaterThanOrEqualToThreshold notBreaching "$asg_dimensions"
ensure_alarm interoves-green-web-low-disk \
    "Green web root filesystem has less than 1 GiB free" InterOves/Web \
    disk_free Minimum 60 3 2 1073741824 LessThanThreshold notBreaching "$asg_dimensions"
ensure_alarm interoves-green-web-kernel-oom \
    "Linux kernel logged an out-of-memory kill on Green" InterOves/Web \
    KernelOomEvents Sum 60 1 1 1 GreaterThanOrEqualToThreshold notBreaching "$no_dimensions"
ensure_alarm interoves-green-web-service-failure \
    "systemd reported a Daphne web.service process failure" InterOves/Web \
    WebServiceFailures Sum 60 1 1 1 GreaterThanOrEqualToThreshold notBreaching "$no_dimensions"

echo "Green observability IAM, log groups, metric filters and alarms applied."
