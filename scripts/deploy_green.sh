#!/usr/bin/env bash
# Prepare and, only with --deploy, release the production Green ALB bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
APP="interoves"
ENV_NAME="interoves-web-green-lb"
if [[ -n "${GREEN_ENV_NAME:-}" && "$GREEN_ENV_NAME" != "$ENV_NAME" ]]; then
    echo "Refusing unexpected Green target '$GREEN_ENV_NAME'; production is $ENV_NAME." >&2
    exit 2
fi
DO_DEPLOY=0
MODE_SET=0
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
        *) echo "Usage: $0 [--dry-run|--deploy]" >&2; exit 2 ;;
    esac
done

if [[ "$DO_DEPLOY" == "1" ]]; then
    echo "Target: $ENV_NAME (production Green ALB)"
    "$ROOT/scripts/deploy_preflight.sh"
else
    echo "Dry run: target would be $ENV_NAME (no AWS mutation)"
fi

workdir="$(mktemp -d /tmp/interoves-green-deploy.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT
bundle="$workdir/interoves-green.zip"
"$ROOT/scripts/prepare_eb_bundle.sh" "$ENV_NAME" "$bundle" 1

if [[ "$DO_DEPLOY" == "1" ]]; then
    # Green bundles intentionally skip EB-side collectstatic because the live
    # instances do not have S3 write permissions. Publish the matching static
    # sources explicitly before releasing the application bundle.
    "$ROOT/scripts/publish_static.sh"
else
    echo "Skipping static publish (dry run)."
fi

sha="${DEPLOY_SOURCE_SHA:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)}"
label="app-green-${sha}-$(date -u +%Y%m%d%H%M%S)"
if [[ "$DO_DEPLOY" == "0" ]]; then
    echo "Prepared version label: $label"
    exit 0
fi

source "$ROOT/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$ROOT"
account_id=$("$ROOT/scripts/aws_with_role.sh" aws sts get-caller-identity --query Account --output text)
bucket="elasticbeanstalk-${REGION}-${account_id}"
key="interoves/green/${label}.zip"
"$ROOT/scripts/aws_with_role.sh" aws s3 cp "$bundle" "s3://${bucket}/${key}" --region "$REGION" --only-show-errors
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk create-application-version \
    --region "$REGION" --application-name "$APP" --version-label "$label" \
    --source-bundle S3Bucket="$bucket",S3Key="$key" >/dev/null
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk update-environment \
    --region "$REGION" --application-name "$APP" --environment-name "$ENV_NAME" \
    --version-label "$label" >/dev/null
"$ROOT/scripts/wait_for_eb_deployment.sh" "$ENV_NAME" "$label" "$REGION"
"$ROOT/scripts/smoke_prod_pages.sh"
echo "Green deploy complete: $label"
