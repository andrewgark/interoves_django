#!/usr/bin/env bash
# Publish the current checkout's static files to the production S3 bucket.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${DEPLOY_SOURCE_DIR:-$ROOT}"
PYTHON="${PYTHON:-$ROOT/../venv/interoves_django/bin/python}"
AWS_PROFILE_NAME="${STATIC_AWS_PROFILE:-interoves}"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET="${AWS_STORAGE_BUCKET_NAME:-interoves-django-static}"
MARKER_KEY="static/.interoves-source-fingerprint"

[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }
[[ "$BUCKET" == "interoves-django-static" ]] || {
    echo "Refusing unexpected static bucket: $BUCKET" >&2
    exit 2
}

echo "Publishing static files to s3://${BUCKET}/static/ (profile=${AWS_PROFILE_NAME})"
cd "$SOURCE_ROOT"

CURRENT="$(bash scripts/static_source_fingerprint.sh)"
existing="$(AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    aws s3 cp "s3://${BUCKET}/${MARKER_KEY}" - --only-show-errors 2>/dev/null || true)"
if [[ "$existing" == "$CURRENT" ]]; then
    echo "Static sources unchanged ($CURRENT); skipping collectstatic."
    exit 0
fi

AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    USE_S3=1 IS_PROD=1 AWS_STORAGE_BUCKET_NAME="$BUCKET" \
    "$PYTHON" manage.py collectstatic --noinput \
        --ignore 'eurovision_booklet' --ignore 'templates'

printf '%s\n' "$CURRENT" | AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    aws s3 cp - "s3://${BUCKET}/${MARKER_KEY}" --only-show-errors \
    --content-type text/plain --cache-control no-cache
echo "Static source fingerprint recorded: $CURRENT"
