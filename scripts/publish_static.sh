#!/usr/bin/env bash
# Publish the current checkout's static files to the production S3 bucket.
set -euo pipefail

MODE="${1:-publish}"
[[ "$MODE" == "publish" || "$MODE" == "--verify-only" ]] || {
    echo "Usage: $0 [--verify-only]" >&2
    exit 2
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${DEPLOY_SOURCE_DIR:-$ROOT}"
PYTHON="${PYTHON:-$ROOT/../venv/interoves_django/bin/python}"
AWS_PROFILE_NAME="${STATIC_AWS_PROFILE:-interoves}"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET="${AWS_STORAGE_BUCKET_NAME:-interoves-django-static}"
[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }
[[ "$BUCKET" == "interoves-django-static" ]] || {
    echo "Refusing unexpected static bucket: $BUCKET" >&2
    exit 2
}

echo "Checking and publishing immutable static release (profile=${AWS_PROFILE_NAME})"
cd "$SOURCE_ROOT"

CURRENT="$(bash scripts/static_source_fingerprint.sh)"
RELEASE_ID="$(printf '%s' "${STATIC_RELEASE_ID:-$CURRENT}" | tr -cd 'A-Za-z0-9._-')"
[[ -n "$RELEASE_ID" && "$RELEASE_ID" == "$CURRENT" ]] || {
    echo "STATIC_RELEASE_ID does not match the static source fingerprint." >&2
    exit 2
}
STATIC_PREFIX="static/releases/${RELEASE_ID}"
MARKER_KEY="${STATIC_PREFIX}/.source-fingerprint"
echo "Publishing static files to s3://${BUCKET}/${STATIC_PREFIX}/"
marker_key_found="$(AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$MARKER_KEY" \
        --max-keys 1 --query 'Contents[0].Key' --output text)"
existing=""
if [[ "$marker_key_found" == "$MARKER_KEY" ]]; then
    marker_file="$(mktemp)"
    trap 'rm -f "$marker_file"' EXIT
    AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
        aws s3 cp "s3://${BUCKET}/${MARKER_KEY}" "$marker_file" --only-show-errors
    existing="$(<"$marker_file")"
fi
if [[ "$existing" == "$CURRENT" ]]; then
    echo "Static sources unchanged ($CURRENT); skipping collectstatic."
    exit 0
fi
if [[ "$MODE" == "--verify-only" ]]; then
    echo "Static release $RELEASE_ID is not fully published; refusing observe-only rollout." >&2
    exit 1
fi

AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    USE_S3=1 IS_PROD=1 STATIC_RELEASE_ID="$RELEASE_ID" \
    AWS_STORAGE_BUCKET_NAME="$BUCKET" \
    "$PYTHON" manage.py collectstatic --noinput \
        --ignore 'eurovision_booklet' --ignore 'templates'

printf '%s\n' "$CURRENT" | AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    aws s3 cp - "s3://${BUCKET}/${MARKER_KEY}" --only-show-errors \
    --content-type text/plain --cache-control no-cache
echo "Static source fingerprint recorded: $CURRENT"
