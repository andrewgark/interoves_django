#!/usr/bin/env bash
# Prepare an EB source bundle from the live bundle of an explicit environment.
# This script never updates EB.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
APP="interoves"
ENV_NAME="${1:-}"
OUTPUT_ZIP="${2:-}"
SKIP_COLLECTSTATIC="${3:-0}"

if [[ -z "$ENV_NAME" || -z "$OUTPUT_ZIP" ]]; then
    echo "Usage: $0 ENVIRONMENT OUTPUT_ZIP [SKIP_COLLECTSTATIC=0|1]" >&2
    exit 2
fi
if [[ "$ENV_NAME" == "interoves-env" ]]; then
    echo "Refusing implicit Blue packaging; use an explicit Blue-only workflow." >&2
    exit 2
fi
for tool in unzip rsync zip; do
    command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done

workdir="$(mktemp -d /tmp/interoves-eb-bundle.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT
live_zip="$workdir/live.zip"
stage="$workdir/stage"
rsync_filters="$workdir/rsync.filters"
mkdir -p "$stage"

# .ebignore uses gitignore syntax; rsync needs an explicit filter prefix.
while IFS= read -r pattern || [[ -n "$pattern" ]]; do
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue
    if [[ "$pattern" == !* ]]; then
        printf '+ %s\n' "${pattern#!}" >> "$rsync_filters"
    else
        printf -- '- %s\n' "$pattern" >> "$rsync_filters"
    fi
done < "$ROOT/.ebignore"

current_version=$("$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-environments \
    --region "$REGION" --application-name "$APP" --environment-names "$ENV_NAME" \
    --query 'Environments[0].VersionLabel' --output text)
if [[ -z "$current_version" || "$current_version" == "None" ]]; then
    echo "Could not resolve current version for $ENV_NAME" >&2
    exit 1
fi

bundle_meta=$("$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-application-versions \
    --region "$REGION" --application-name "$APP" --version-label "$current_version" \
    --query 'Versions[0].SourceBundle.[S3Bucket,S3Key]' --output text 2>/dev/null || true)
read -r source_bucket source_key <<< "$bundle_meta"
if [[ -z "$source_bucket" || -z "$source_key" || "$source_bucket" == "None" || "$source_key" == "None" ]]; then
    account_id=$("$ROOT/scripts/aws_with_role.sh" aws sts get-caller-identity --query Account --output text)
    source_bucket="elasticbeanstalk-${REGION}-${account_id}"
    source_key=""
    for candidate in "${APP}/${current_version}.zip" "${APP}/green/${current_version}.zip" "${APP}/workers/${current_version}.zip"; do
        if "$ROOT/scripts/aws_with_role.sh" aws s3api head-object --region "$REGION" \
            --bucket "$source_bucket" --key "$candidate" >/dev/null 2>&1; then
            source_key="$candidate"
            break
        fi
    done
    if [[ -z "$source_key" ]]; then
        echo "Could not resolve source bundle for $ENV_NAME version $current_version." >&2
        echo "Neither EB metadata nor retained standard/green/worker S3 objects exist." >&2
        exit 1
    fi
    echo "Using retained EB S3 object ${source_bucket}/${source_key} (application-version metadata is absent)." >&2
fi

echo "Downloading live source bundle for $ENV_NAME version $current_version"
"$ROOT/scripts/aws_with_role.sh" aws s3 cp "s3://${source_bucket}/${source_key}" "$live_zip" --region "$REGION" --only-show-errors
unzip -q "$live_zip" -d "$stage"

# .ebignore filters checkout files; live EB configuration is protected from the checkout.
rsync -a --delete-delay \
    --filter="merge $rsync_filters" \
    --exclude='/.ebextensions/***' \
    --exclude='/.platform/***' \
    --exclude='/.elasticbeanstalk/***' \
    "$ROOT/" "$stage/"

if [[ "$SKIP_COLLECTSTATIC" == "1" ]]; then
    collectstatic="$stage/scripts/collectstatic_if_changed.sh"
    [[ -f "$collectstatic" ]] || { echo "Missing collectstatic script in bundle" >&2; exit 1; }
    grep -q '^set -euo pipefail$' "$collectstatic" || {
        echo "Unexpected collectstatic script shape; refusing zip-only patch" >&2
        exit 1
    }
    sed -i '/^set -euo pipefail$/a exit 0 # Green deploy: static is published separately' "$collectstatic"
fi

sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)"
printf '%s\n' "$sha" > "$stage/interoves_django/deploy_version.txt"

mkdir -p "$(dirname "$OUTPUT_ZIP")"
rm -f "$OUTPUT_ZIP"
(cd "$stage" && zip -q -r "$OUTPUT_ZIP" .)
echo "Prepared bundle: $OUTPUT_ZIP"
