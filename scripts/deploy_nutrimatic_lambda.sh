#!/usr/bin/env bash
# Build the Nutrimatic Lambda image and deploy stack interoves-nutrimatic.
#
#   ./scripts/deploy_nutrimatic_lambda.sh            # build, push, deploy
#   AWS_PROFILE=default ./scripts/deploy_nutrimatic_lambda.sh
#
# The image holds find-expr, cgi-search.py (from nutrimatic_bundle/) and the
# production index from S3 (cached under ~/.cache/interoves/nutrimatic).
# The tag is the git hash of those inputs plus the index's ETag, so a rerun
# without changes reuses the pushed image. Needs the `default` profile:
# ai-bot cannot create IAM roles, ECR repositories or Lambda functions.
set -euo pipefail

cd "$(dirname "$0")/.."
REGION=eu-central-1
STACK=interoves-nutrimatic
TEMPLATE=infra/nutrimatic/template.yaml
INDEX_BUCKET=interoves-django-static
INDEX_KEY=private/nutrimatic/wiki-merged.packed.index
CACHE_DIR="$HOME/.cache/interoves/nutrimatic"
export AWS_PROFILE="${AWS_PROFILE:-default}"
aws() { command aws --region "$REGION" "$@"; }

deploy_stack() {
    aws cloudformation deploy --stack-name "$STACK" --template-file "$TEMPLATE" \
        --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
        --parameter-overrides "$@"
}

# 1. Repository first (the function cannot be created before an image exists).
deploy_stack ImageTag=
REPO_URI=$(aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" --output text)

# 2. Index from S3, re-downloaded only when its ETag changes.
mkdir -p "$CACHE_DIR"
ETAG=$(aws s3api head-object --bucket "$INDEX_BUCKET" --key "$INDEX_KEY" --query ETag --output text | tr -dc 'a-f0-9')
INDEX="$CACHE_DIR/wiki-merged.packed.index"
if [[ "$(cat "$CACHE_DIR/etag" 2>/dev/null)" != "$ETAG" || ! -f "$INDEX" ]]; then
    aws s3 cp "s3://$INDEX_BUCKET/$INDEX_KEY" "$INDEX.part" --only-show-errors
    mv "$INDEX.part" "$INDEX"
    echo "$ETAG" >"$CACHE_DIR/etag"
fi

INPUTS=(infra/nutrimatic/Dockerfile infra/nutrimatic/handler.py
        nutrimatic_bundle/cgi_scripts/cgi-search.py nutrimatic_bundle/build/find-expr)
SRC_HASH=$(cat "${INPUTS[@]}" | sha256sum | cut -c1-12)
TAG="n-${SRC_HASH}-${ETAG:0:8}"

# 3. Build and push unless this tag is already in ECR.
if ! aws ecr describe-images --repository-name "${REPO_URI#*/}" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
    CTX=$(mktemp -d)
    trap 'rm -rf "$CTX"' EXIT
    cp "${INPUTS[@]}" "$CTX/"
    ln "$INDEX" "$CTX/" 2>/dev/null || cp "$INDEX" "$CTX/"
    docker build --platform linux/amd64 --provenance=false -t "$REPO_URI:$TAG" "$CTX"
    aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO_URI%%/*}"
    docker push "$REPO_URI:$TAG"
fi

# 4. Function, IAM, warmup and alarms on this image.
deploy_stack ImageTag="$TAG"
echo "Deployed $STACK with image tag $TAG"
