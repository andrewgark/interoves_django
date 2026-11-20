#!/usr/bin/env bash
# Build and publish one immutable worker image, then print its digest URI.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
AWS_PROFILE_NAME="${AWS_PROFILE:-interoves}"
REPOSITORY="${WORKER_ECR_REPOSITORY:-interoves-workers}"
COMMIT="${1:-$(git -C "$ROOT" rev-parse --short HEAD)}"
[[ "$COMMIT" =~ ^[[:alnum:]][[:alnum:]._-]*$ ]] || {
    echo "Invalid image commit/tag: $COMMIT" >&2
    exit 2
}

aws_cmd() { AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" aws "$@"; }
ACCOUNT_ID="$(aws_cmd sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_TAG="${REGISTRY}/${REPOSITORY}:${COMMIT}"

echo "Logging in to ${REGISTRY} as AWS profile ${AWS_PROFILE_NAME}"
aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
echo "Building ${IMAGE_TAG} from commit ${COMMIT}"
# ECR currently accepts the standard image manifest but rejects BuildKit's
# OCI attestation manifest with the deploy user's minimal repository policy.
docker build --pull --provenance=false --file "$ROOT/Dockerfile.worker" --tag "$IMAGE_TAG" "$ROOT"
docker push "$IMAGE_TAG" >/dev/null

DIGEST="$(aws_cmd ecr describe-images --repository-name "$REPOSITORY" --image-ids imageTag="$COMMIT" --query 'imageDetails[0].imageDigest' --output text)"
[[ -n "$DIGEST" && "$DIGEST" != None ]] || { echo "Could not resolve pushed image digest" >&2; exit 1; }
echo "${REGISTRY}/${REPOSITORY}@${DIGEST}"
