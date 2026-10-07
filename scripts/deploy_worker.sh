#!/usr/bin/env bash
# Prepare and, only with --deploy, release an explicitly named worker bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
APP="interoves"
SOURCE_SHA="$(git -C "$ROOT" rev-parse HEAD)"
SOURCE_DIR="$(mktemp -d /tmp/interoves-worker-source.XXXXXX)"
trap 'rm -rf "$SOURCE_DIR"' EXIT
git -C "$ROOT" archive "$SOURCE_SHA" | tar -x -C "$SOURCE_DIR"
export DEPLOY_SOURCE_DIR="$SOURCE_DIR"
export DEPLOY_SOURCE_SHA="$(git -C "$ROOT" rev-parse --short "$SOURCE_SHA")"
ENV_NAME="${1:-}"
DO_DEPLOY=0
MODE_SET=0
case "$ENV_NAME" in
    interoves-recheck-worker|interoves-background-worker|interoves-identity-worker|interoves-integrations-worker) ;;
    *) echo "Usage: $0 WORKER_ENVIRONMENT [--dry-run|--deploy]" >&2; exit 2 ;;
esac
shift
for arg in "$@"; do
    case "$arg" in
        --deploy)
            [[ "$MODE_SET" == "0" ]] || { echo "Choose exactly one of --dry-run and --deploy." >&2; exit 2; }
            DO_DEPLOY=1; MODE_SET=1
            ;;
        --dry-run|--prepare-only)
            [[ "$MODE_SET" == "0" ]] || { echo "Choose exactly one of --dry-run and --deploy." >&2; exit 2; }
            DO_DEPLOY=0; MODE_SET=1
            ;;
        *) echo "Usage: $0 WORKER_ENVIRONMENT [--dry-run|--deploy]" >&2; exit 2 ;;
    esac
done

if [[ "$ENV_NAME" == "interoves-recheck-worker" ]]; then
    "$ROOT/scripts/recheck_preflight.sh" $([[ "$DO_DEPLOY" == "1" ]] && echo --schema)
fi

workdir="$(mktemp -d /tmp/interoves-worker-deploy.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT
bundle="$workdir/${ENV_NAME}.zip"
"$ROOT/scripts/prepare_eb_bundle.sh" "$ENV_NAME" "$bundle" 0
sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)"
label="app-${ENV_NAME#interoves-}-${sha}-$(date -u +%Y%m%d%H%M%S)"
[[ "$DO_DEPLOY" == "1" ]] || { echo "Prepared worker version label: $label"; exit 0; }

source "$ROOT/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$ROOT"
account_id=$("$ROOT/scripts/aws_with_role.sh" aws sts get-caller-identity --query Account --output text)
bucket="elasticbeanstalk-${REGION}-${account_id}"
key="interoves/workers/${label}.zip"
"$ROOT/scripts/aws_with_role.sh" aws s3 cp "$bundle" "s3://${bucket}/${key}" --region "$REGION" --only-show-errors
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk create-application-version \
    --region "$REGION" --application-name "$APP" --version-label "$label" \
    --source-bundle S3Bucket="$bucket",S3Key="$key" >/dev/null
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk update-environment \
    --region "$REGION" --application-name "$APP" --environment-name "$ENV_NAME" \
    --version-label "$label" >/dev/null
"$ROOT/scripts/wait_for_eb_deployment.sh" "$ENV_NAME" "$label" "$REGION" worker
echo "Worker deploy complete: $ENV_NAME / $label"
