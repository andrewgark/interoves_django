#!/usr/bin/env bash
# Plan or deploy one ECS worker service. Secret values never leave Secrets Manager.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
AWS_PROFILE_NAME="${AWS_PROFILE:-interoves}"
DEPLOY_PROFILE="normal"
WORKER="${1:-}"
IMAGE_URI="${2:-}"
IMAGE_COMMIT="${IMAGE_COMMIT:-}"
APPLY=0
DESIRED_COUNT=0
MODE=""
# ECS workers currently run in the VPC's public subnets.  Keep these defaults
# aligned with the live services so an ordinary deploy does not accidentally
# place Fargate tasks in the isolated legacy subnets, where they cannot reach
# ECR without NAT/VPC endpoints.
ECS_SUBNET_IDS="${INTEROVES_ECS_SUBNET_IDS:-subnet-df8b26a2,subnet-29483d42}"
ECS_ASSIGN_PUBLIC_IP="${INTEROVES_ECS_ASSIGN_PUBLIC_IP:-ENABLED}"

usage() {
    echo "Usage: $0 WORKER IMAGE_URI [--image-commit SHA] [--profile quiet|normal|game-day] [--mode ecs-fargate|ecs-fargate-spot] [--desired-count N] [--apply]" >&2
    exit 2
}

case "$WORKER" in background|identity|integrations|recheck) ;; *) usage ;; esac
[[ -n "$IMAGE_URI" ]] || usage
shift 2
PROFILE_MODE=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --image-commit) IMAGE_COMMIT="${2:-}"; shift 2 ;;
        --profile) DEPLOY_PROFILE="${2:-}"; shift 2 ;;
        --mode) MODE="${2:-}"; shift 2 ;;
        --desired-count) DESIRED_COUNT="${2:-}"; shift 2 ;;
        --apply) APPLY=1; shift ;;
        *) usage ;;
    esac
done

case "$DEPLOY_PROFILE" in
    quiet) [[ "$WORKER" == background ]] && PROFILE_MODE=lambda || PROFILE_MODE=ecs-fargate-spot ;;
    normal) [[ "$WORKER" == background || "$WORKER" == recheck ]] && PROFILE_MODE=ecs-fargate-spot || PROFILE_MODE=ecs-fargate ;;
    game-day) PROFILE_MODE=ecs-fargate ;;
    *) echo "Unsupported profile: $DEPLOY_PROFILE" >&2; exit 2 ;;
esac
MODE="${MODE:-$PROFILE_MODE}"
case "$MODE" in ecs-fargate|ecs-fargate-spot) ;; *) echo "This script deploys ECS modes only: $MODE" >&2; exit 2 ;; esac
if [[ "$WORKER" == integrations && "$MODE" == ecs-fargate-spot ]]; then
    echo "Integrations worker is On-Demand only until external side effects are verified." >&2
    exit 2
fi
[[ "$DESIRED_COUNT" =~ ^[0-9]+$ ]] || { echo "Desired count must be a non-negative integer." >&2; exit 2; }
if [[ -z "$IMAGE_COMMIT" ]]; then
    IMAGE_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
fi
[[ -n "$IMAGE_COMMIT" ]] || { echo "Could not determine image commit; pass --image-commit SHA." >&2; exit 2; }

PYTHON="$ROOT/../venv/interoves_django/bin/python"
[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }

# Use a dedicated least-privilege deploy role. The base interoves profile is
# only used to assume it; default/root credentials are never needed by deploy.
DEPLOY_ROLE_ARN="${INTEROVES_ECS_DEPLOY_ROLE_ARN:-arn:aws:iam::916000456640:role/interoves-ecs-deployer}"
ROLE_SESSION="interoves-ecs-deploy-$(hostname -s)-$$"
assume_json="$(AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" aws sts assume-role \
    --role-arn "$DEPLOY_ROLE_ARN" --role-session-name "$ROLE_SESSION" \
    --duration-seconds 3600 --output json)"
read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(
    printf '%s' "$assume_json" | "$PYTHON" -c '
import json, sys
c = json.load(sys.stdin)["Credentials"]
print(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
'
)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_PROFILE
aws_cmd() {
    AWS_DEFAULT_REGION="$REGION" aws "$@"
}

ENVIRONMENT="interoves-${WORKER}-worker"
[[ "$WORKER" == recheck ]] && ENVIRONMENT=interoves-recheck-worker
env_json="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name interoves --environment-name "$ENVIRONMENT" --query 'ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environment`].{name:OptionName,value:Value}' --output json)"
secret_names_json="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name interoves --environment-name "$ENVIRONMENT" --query 'ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environmentsecrets`].OptionName' --output json)"
# The legacy recheck EB environment predates the shared Tribute/Telegram
# settings required by Django system checks.  Reuse only missing shared
# settings from identity; keep all recheck-specific values authoritative.
if [[ "$WORKER" == recheck ]]; then
    shared_env_json="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name interoves --environment-name interoves-identity-worker --query 'ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environment`].{name:OptionName,value:Value}' --output json)"
    shared_secret_names_json="$(aws_cmd elasticbeanstalk describe-configuration-settings --application-name interoves --environment-name interoves-identity-worker --query 'ConfigurationSettings[0].OptionSettings[?Namespace==`aws:elasticbeanstalk:application:environmentsecrets`].OptionName' --output json)"
    env_json="$(printf '%s\0%s\0' "$env_json" "$shared_env_json" | "$PYTHON" -c '
import json, sys
base, shared = [json.loads(item) for item in sys.stdin.buffer.read().split(b"\0")[:2]]
names = {item["name"] for item in base}
base.extend(item for item in shared if item["name"] not in names)
print(json.dumps(base, separators=(",", ":")))
')"
    secret_names_json="$(printf '%s\0%s\0' "$secret_names_json" "$shared_secret_names_json" | "$PYTHON" -c '
import json, sys
base, shared = [json.loads(item) for item in sys.stdin.buffer.read().split(b"\0")[:2]]
seen = set(base)
for name in shared:
    if name not in seen:
        base.append(name)
        seen.add(name)
print(json.dumps(base, separators=(",", ":")))
')"
fi
all_secrets_json="$(aws_cmd secretsmanager list-secrets --query "SecretList[?starts_with(Name, 'interoves/production/')].{name:Name,arn:ARN}" --output json)"
config_json="$(printf '%s\0%s\0%s\0' "$env_json" "$secret_names_json" "$all_secrets_json" | "$PYTHON" -c '
import json, sys
env, names, secrets = [json.loads(item) for item in sys.stdin.buffer.read().split(b"\0")[:3]]
values = {item["name"]: item.get("value", "") for item in env}
arns = {item["name"]: item["arn"] for item in secrets}
secret_map = {}
for name in names:
    arn = arns.get("interoves/production/" + name)
    if not arn:
        raise SystemExit("Missing Secrets Manager reference: " + name)
    secret_map[name] = arn
print(json.dumps({"values": values, "secret_map": secret_map}, separators=(",", ":")))
')"
value() { printf '%s' "$config_json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["values"].get(sys.argv[1], ""))' "$1"; }
secret_map="$(printf '%s' "$config_json" | "$PYTHON" -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["secret_map"], separators=(",", ":")))')"
secret_arns="$(printf '%s' "$config_json" | "$PYTHON" -c 'import json,sys; print(",".join(json.load(sys.stdin)["secret_map"].values()))')"
RDS_SECRET_ARN="$(value RDS_SECRET_ARN)"
[[ -n "$RDS_SECRET_ARN" && "$RDS_SECRET_ARN" != None ]] || { echo "RDS_SECRET_ARN missing in $ENVIRONMENT" >&2; exit 1; }
secret_arns="$secret_arns,$RDS_SECRET_ARN"

QUEUE_NAME="interoves-${WORKER}"
QUEUE_URL="$(aws_cmd sqs get-queue-url --queue-name "$QUEUE_NAME" --query QueueUrl --output text)"
QUEUE_ARN="$(aws_cmd sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
RUNTIME_ROLE="${WORKER}-worker"
[[ "$WORKER" == recheck ]] && RUNTIME_ROLE=worker
[[ "$WORKER" == integrations ]] && RUNTIME_ROLE=integration-worker
TASK_ROLE_STACK="interoves-${WORKER}-task-role"
SERVICE_STACK="interoves-${WORKER}-ecs-service"
TASK_ROLE_ARN="$(aws_cmd cloudformation describe-stacks --stack-name "$TASK_ROLE_STACK" --query "Stacks[0].Outputs[?OutputKey=='TaskRoleArn'].OutputValue" --output text 2>/dev/null || true)"
EXECUTION_ROLE_ARN="$(aws_cmd cloudformation describe-stacks --stack-name interoves-ecs-foundation --query "Stacks[0].Outputs[?OutputKey=='ExecutionRoleArn'].OutputValue" --output text)"

case "$ECS_ASSIGN_PUBLIC_IP" in ENABLED|DISABLED) ;; *) echo "INTEROVES_ECS_ASSIGN_PUBLIC_IP must be ENABLED or DISABLED" >&2; exit 2 ;; esac
[[ "$ECS_SUBNET_IDS" == subnet-* ]] || { echo "INTEROVES_ECS_SUBNET_IDS must be a comma-separated subnet list." >&2; exit 2; }

echo "worker=$WORKER environment=$ENVIRONMENT profile=$DEPLOY_PROFILE aws_profile=$AWS_PROFILE_NAME mode=$MODE desired_count=$DESIRED_COUNT subnet_ids=$ECS_SUBNET_IDS assign_public_ip=$ECS_ASSIGN_PUBLIC_IP"
echo "image=$IMAGE_URI image_commit=$IMAGE_COMMIT queue=$QUEUE_NAME service_stack=$SERVICE_STACK"
if [[ "$APPLY" != 1 ]]; then echo "plan_only=true (pass --apply to change AWS)"; exit 0; fi

aws_cmd cloudformation deploy --stack-name "$TASK_ROLE_STACK" --template-file "$ROOT/infra/ecs/worker-task-role.yaml" --parameter-overrides WorkerName="$WORKER" QueueArn="$QUEUE_ARN" ConfigSecretArns="$secret_arns" --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset
TASK_ROLE_ARN="$(aws_cmd cloudformation describe-stacks --stack-name "$TASK_ROLE_STACK" --query "Stacks[0].Outputs[?OutputKey=='TaskRoleArn'].OutputValue" --output text)"
plain_env_json="$(printf '%s' "$config_json" | "$PYTHON" -c '
import json, sys
values = json.load(sys.stdin)["values"]
reserved = {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_PROFILE", "DJANGO_SETTINGS_MODULE", "INTEROVES_CONFIG_SECRET_ID",
    "INTEROVES_CONFIG_VALUES", "INTEROVES_CONFIG_PROFILE", "PYTHONPATH",
}
print(json.dumps({key: value for key, value in values.items() if key not in reserved}, separators=(",", ":")))
')"
aws_cmd cloudformation deploy --stack-name "$SERVICE_STACK" --template-file "$ROOT/infra/ecs/worker-service.yaml" --parameter-overrides ClusterName=interoves-workers ServiceName="interoves-${WORKER}-ecs" WorkerName="$WORKER" RuntimeRole="$RUNTIME_ROLE" DeploymentMode="$MODE" ImageUri="$IMAGE_URI" ImageCommit="$IMAGE_COMMIT" TaskExecutionRoleArn="$EXECUTION_ROLE_ARN" TaskRoleArn="$TASK_ROLE_ARN" QueueUrl="$QUEUE_URL" ConfigSecretArn='' ConfigSecretMap="$secret_map" ConfigEnvJson="$plain_env_json" SubnetIds="$ECS_SUBNET_IDS" SecurityGroupIds=sg-0e8becbf991186aba AssignPublicIp="$ECS_ASSIGN_PUBLIC_IP" DesiredCount="$DESIRED_COUNT" LogGroupName="/interoves/workers/$WORKER" RdsSecretArn="$RDS_SECRET_ARN" RdsDbName="$(value RDS_DB_NAME)" RdsHostname="$(value RDS_HOSTNAME)" RdsPort="$(value RDS_PORT)" RdsUsername="$(value RDS_USERNAME)" RedisHost="$(value REDIS_HOST)" RedisPort="$(value REDIS_PORT)" RedisTls="$(value REDIS_TLS)" IsProd="$(value IS_PROD)" DebugOn="$(value DEBUG_ON)" AsgiThreads="$(value ASGI_THREADS)" ExtraAllowedHosts="$(value EXTRA_ALLOWED_HOSTS)" TributeClubSubscriptionEurId="$(value TRIBUTE_CLUB_SUBSCRIPTION_EUR_ID)" TributeClubSubscriptionEurUrl="$(value TRIBUTE_CLUB_SUBSCRIPTION_EUR_URL)" --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset
