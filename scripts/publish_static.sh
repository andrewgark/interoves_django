#!/usr/bin/env bash
# Publish the current checkout's static files to the production S3 bucket.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/../venv/interoves_django/bin/python}"
AWS_PROFILE_NAME="${STATIC_AWS_PROFILE:-interoves}"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
BUCKET="${AWS_STORAGE_BUCKET_NAME:-interoves-django-static}"

[[ -x "$PYTHON" ]] || { echo "Missing project venv: $PYTHON" >&2; exit 2; }
[[ "$BUCKET" == "interoves-django-static" ]] || {
    echo "Refusing unexpected static bucket: $BUCKET" >&2
    exit 2
}

echo "Publishing static files to s3://${BUCKET}/static/ (profile=${AWS_PROFILE_NAME})"
cd "$ROOT"
AWS_PROFILE="$AWS_PROFILE_NAME" AWS_DEFAULT_REGION="$REGION" \
    USE_S3=1 IS_PROD=1 AWS_STORAGE_BUCKET_NAME="$BUCKET" \
    "$PYTHON" manage.py collectstatic --noinput
