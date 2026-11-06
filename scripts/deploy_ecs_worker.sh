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
DESIRED_COUNT=""
DESIRED_COUNT_SET=0
MODE=""
# ECS workers currently run in the VPC's public subnets.  Keep these defaults
# aligned with the live services so an ordinary deploy does not accidentally
# place Fargate tasks in the isolated legacy subnets, where they cannot reach
# ECR without NAT/VPC endpoints.  Only eu-central-1a: RDS and Redis live
# there, so tasks in other AZs pay for cross-AZ traffic on every query.
ECS_SUBNET_IDS="${INTEROVES_ECS_SUBNET_IDS:-subnet-29483d42}"
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
        --desired-count)
            [[ "$#" -ge 2 && "${2}" =~ ^[0-9]+$ ]] || { echo "--desired-count requires a non-negative integer." >&2; usage; }
            DESIRED_COUNT="${2}"
            DESIRED_COUNT_SET=1
            shift 2
            ;;
        --apply) APPLY=1; shift ;;
        *) usage ;;
    esac
done

if [[ "$APPLY" == "1" && "$DESIRED_COUNT_SET" != "1" ]]; then
    echo "Refusing ECS apply without --desired-count; pass the intended service task count explicitly." >&2
    exit 2
fi

case "$DEPLOY_PROFILE" in
    quiet) [[ "$WORKER" == background ]] && PROFILE_MODE=lambda || PROFILE_MODE=ecs-fargate-spot ;;
    normal) PROFILE_MODE=ecs-fargate-spot ;;
    game-day) PROFILE_MODE=ecs-fargate ;;
    *) echo "Unsupported profile: $DEPLOY_PROFILE" >&2; exit 2 ;;
esac
MODE="${MODE:-$PROFILE_MODE}"
case "$MODE" in ecs-fargate|ecs-fargate-spot) ;; *) echo "This script deploys ECS modes only: $MODE" >&2; exit 2 ;; esac

PYTHON="$ROOT/../venv/interoves_django/bin/python"
[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }
read -r CPU MEMORY < <("$PYTHON" - "$ROOT/infra/ecs/worker-profiles.yaml" "$WORKER" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as source:
    profiles = yaml.safe_load(source)
worker = profiles["workers"][sys.argv[2]]
print(worker["cpu"], worker["memory"])
PY
)
DESIRED_COUNT="${DESIRED_COUNT:-unspecified}"
if [[ -z "$IMAGE_COMMIT" ]]; then
    IMAGE_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
fi
[[ -n "$IMAGE_COMMIT" ]] || { echo "Could not determine image commit; pass --image-commit SHA." >&2; exit 2; }

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

CONFIG_FILE="$ROOT/infra/ecs/production-worker-config.json"
# Plain values are versioned with the deployment code. The task template is
# authoritative for secret parameter references; neither depends on legacy EB.
all_params_json="$(aws_cmd ssm describe-parameters --parameter-filters Key=Path,Option=OneLevel,Values=/interoves/production --query 'Parameters[].Name' --output json)"
config_json="$("$PYTHON" - "$CONFIG_FILE" "$ROOT/infra/ecs/worker-service.yaml" "$WORKER" "$all_params_json" <<'PY'
import json, re, sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
if manifest.get('schema_version') != 1:
    raise SystemExit('Unsupported worker configuration schema')
values = manifest['workers'][sys.argv[3]]
if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
    raise SystemExit('Worker configuration must contain string values')
if any(re.search(r'(TOKEN|PASSWORD|CHAT_ID|API_HASH|SESSION|PRIVATE_KEY)', k) for k in values):
    raise SystemExit('Sensitive worker configuration key must use SSM')
template = Path(sys.argv[2]).read_text(encoding='utf-8')
required = set(re.findall(r'parameter/interoves/production/([A-Z0-9_]+)', template))
available = set(json.loads(sys.argv[4]))
missing = sorted('/interoves/production/' + name for name in required if '/interoves/production/' + name not in available)
if missing:
    raise SystemExit('Missing SSM parameters: ' + ', '.join(missing))
print(json.dumps({'values': values}, separators=(',', ':')))
PY
)"
value() { printf '%s' "$config_json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["values"].get(sys.argv[1], ""))' "$1"; }
secret_map='{}'
secret_arns=""
RDS_SECRET_ARN="$(value RDS_SECRET_ARN)"
[[ -n "$RDS_SECRET_ARN" && "$RDS_SECRET_ARN" != None ]] || { echo "RDS_SECRET_ARN missing in $ENVIRONMENT" >&2; exit 1; }
SOCIAL_QUEUE_S3_BUCKET="$(value SOCIAL_QUEUE_S3_BUCKET)"
SOCIAL_QUEUE_S3_BUCKET="${SOCIAL_QUEUE_S3_BUCKET:-interoves-django-static}"
secret_arns="$RDS_SECRET_ARN"

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

echo "worker=$WORKER config_source=infra/ecs/production-worker-config.json profile=$DEPLOY_PROFILE aws_profile=$AWS_PROFILE_NAME mode=$MODE cpu=$CPU memory=$MEMORY desired_count=$DESIRED_COUNT subnet_ids=$ECS_SUBNET_IDS assign_public_ip=$ECS_ASSIGN_PUBLIC_IP"
echo "config_sha256=$(sha256sum "$CONFIG_FILE" | cut -d' ' -f1)"
echo "config_keys=$(printf '%s' "$config_json" | "$PYTHON" -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin)["values"])))')"
echo "image=$IMAGE_URI image_commit=$IMAGE_COMMIT queue=$QUEUE_NAME service_stack=$SERVICE_STACK"
if [[ "$APPLY" != 1 ]]; then echo "plan_only=true (pass --apply to change AWS)"; exit 0; fi

aws_cmd cloudformation deploy --stack-name "$TASK_ROLE_STACK" --template-file "$ROOT/infra/ecs/worker-task-role.yaml" --parameter-overrides WorkerName="$WORKER" QueueArn="$QUEUE_ARN" ConfigSecretArns="$secret_arns" SocialQueueS3Bucket="$SOCIAL_QUEUE_S3_BUCKET" --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset
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
aws_cmd cloudformation deploy --stack-name "$SERVICE_STACK" --template-file "$ROOT/infra/ecs/worker-service.yaml" --parameter-overrides ClusterName=interoves-workers ServiceName="interoves-${WORKER}-ecs" WorkerName="$WORKER" RuntimeRole="$RUNTIME_ROLE" DeploymentMode="$MODE" ImageUri="$IMAGE_URI" ImageCommit="$IMAGE_COMMIT" Cpu="$CPU" Memory="$MEMORY" TaskExecutionRoleArn="$EXECUTION_ROLE_ARN" TaskRoleArn="$TASK_ROLE_ARN" QueueUrl="$QUEUE_URL" ConfigSecretArn='' ConfigSecretMap="$secret_map" ConfigEnvJson="$plain_env_json" SubnetIds="$ECS_SUBNET_IDS" SecurityGroupIds=sg-0e8becbf991186aba AssignPublicIp="$ECS_ASSIGN_PUBLIC_IP" DesiredCount="$DESIRED_COUNT" LogGroupName="/interoves/workers/$WORKER" RdsSecretArn="$RDS_SECRET_ARN" RdsDbName="$(value RDS_DB_NAME)" RdsHostname="$(value RDS_HOSTNAME)" RdsPort="$(value RDS_PORT)" RdsUsername="$(value RDS_USERNAME)" RedisHost="$(value REDIS_HOST)" RedisPort="$(value REDIS_PORT)" RedisTls="$(value REDIS_TLS)" IsProd="$(value IS_PROD)" DebugOn="$(value DEBUG_ON)" AsgiThreads="$(value ASGI_THREADS)" ExtraAllowedHosts="$(value EXTRA_ALLOWED_HOSTS)" TributeClubSubscriptionEurId="$(value TRIBUTE_CLUB_SUBSCRIPTION_EUR_ID)" TributeClubSubscriptionEurUrl="$(value TRIBUTE_CLUB_SUBSCRIPTION_EUR_URL)" --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset
