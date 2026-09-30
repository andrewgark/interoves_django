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
SOURCE_BUNDLE_URI="${EB_BUNDLE_SOURCE_URI:-}"

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

if [[ -z "$SOURCE_BUNDLE_URI" ]]; then
    current_version=$("$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-environments \
        --region "$REGION" --application-name "$APP" --environment-names "$ENV_NAME" \
        --query 'Environments[0].VersionLabel' --output text)
    if [[ -z "$current_version" || "$current_version" == "None" ]]; then
        echo "Could not resolve current version for $ENV_NAME" >&2
        exit 1
    fi

    # Application-version metadata is not consistent across retained EB
    # versions, so prefer the known S3 naming conventions when metadata is
    # absent.  The explicit override below is for recovery when an old
    # worker's original bundle was garbage-collected.
    bundle_meta=$("$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-application-versions \
        --region "$REGION" --application-name "$APP" --version-label "$current_version" \
        --query 'ApplicationVersions[0].SourceBundle.[S3Bucket,S3Key]' --output text 2>/dev/null || true)
    read -r source_bucket source_key <<< "$bundle_meta"
fi
if [[ -n "$SOURCE_BUNDLE_URI" ]]; then
    source_uri="$SOURCE_BUNDLE_URI"
elif [[ -z "${source_bucket:-}" || -z "${source_key:-}" || "$source_bucket" == "None" || "$source_key" == "None" ]]; then
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
    source_uri="s3://${source_bucket}/${source_key}"
elif [[ -n "$source_bucket" && -n "$source_key" ]]; then
    source_uri="s3://${source_bucket}/${source_key}"
fi

if [[ -n "$SOURCE_BUNDLE_URI" ]]; then
    echo "Using explicit source bundle $SOURCE_BUNDLE_URI for $ENV_NAME" >&2
else
    echo "Downloading live source bundle for $ENV_NAME version $current_version"
fi
"$ROOT/scripts/aws_with_role.sh" aws s3 cp "$source_uri" "$live_zip" --region "$REGION" --only-show-errors
unzip -q "$live_zip" -d "$stage"

# .ebignore filters checkout files; live EB configuration is protected from the checkout.
rsync -a --delete-delay \
    --filter="merge $rsync_filters" \
    --exclude='/.ebextensions/***' \
    --exclude='/.platform/***' \
    --exclude='/.elasticbeanstalk/***' \
    "$ROOT/" "$stage/"

# A recovery base can come from the recheck worker when the target worker's
# original bundle has been garbage-collected.  Do not carry recheck-only
# dispatcher state or the generic web Playwright provisioning into the other
# worker roles.
if [[ "$ENV_NAME" != "interoves-recheck-worker" ]]; then
    rm -f \
        "$stage/.platform/recheck-worker.marker" \
        "$stage/.platform/hooks/postdeploy/09_populate_recheck_secret.sh" \
        "$stage/.platform/hooks/postdeploy/10_enable_word_salad_dispatcher.sh" \
        "$stage/.platform/hooks/postdeploy/11_force_recheck_dispatcher.sh" \
        "$stage/.ebextensions/playwright.config"
fi

# Background and identity are legacy SingleInstance environments.  Their
# retained worker configuration must not inherit the recheck base's
# LoadBalanced ASG/health topology.
if [[ "$ENV_NAME" == "interoves-background-worker" || "$ENV_NAME" == "interoves-identity-worker" ]]; then
    rm -f \
        "$stage/.ebextensions/scaling.config" \
        "$stage/.ebextensions/health.config" \
        "$stage/.ebextensions/deploy.config"
fi

# The live Green bundle protects its platform configuration from the checkout.
# The legacy single-instance Green environment is the only target that may
# receive the origin-EIP hook. The active ALB environment must never claim an
# origin EIP; its stable origin is the load balancer DNS name.
if [[ "$ENV_NAME" == "interoves-web-green" ]]; then
    install -D -m 0755 \
        "$ROOT/.platform/hooks/postdeploy/11_associate_green_origin_eip.sh" \
        "$stage/.platform/hooks/postdeploy/11_associate_green_origin_eip.sh"
fi

# The dedicated recheck worker owns the dispatcher hook in this checkout.  The
# rest of the EB configuration remains inherited from the live bundle above.
if [[ "$ENV_NAME" == "interoves-recheck-worker" ]]; then
    # This dedicated queue worker must not run the legacy web/background
    # migration hook during every code deploy.  It can fail on worker-only
    # configuration and block the dispatcher from starting.
    rm -f "$stage/.platform/hooks/postdeploy/02_background_migrations.sh"
    install -D -m 0755 \
        "$ROOT/.platform/hooks/postdeploy/09_populate_recheck_secret.sh" \
        "$stage/.platform/hooks/postdeploy/09_populate_recheck_secret.sh"
    install -D -m 0755 \
        "$ROOT/.platform/hooks/postdeploy/10_enable_word_salad_dispatcher.sh" \
        "$stage/.platform/hooks/postdeploy/10_enable_word_salad_dispatcher.sh"
    : > "$stage/.platform/recheck-worker.marker"
fi

# The integrations worker renders Telegram images with Playwright.  Its live
# EB bundle is intentionally protected above, so it does not inherit the web
# tier's browser provisioning config.  Inject the worker-specific config into
# only this bundle; the other workers do not need Chromium.
if [[ "$ENV_NAME" == "interoves-integrations-worker" ]]; then
    install -D -m 0644 \
        "$ROOT/infra/elasticbeanstalk/future/integrations-worker/playwright.config" \
        "$stage/.ebextensions/playwright-integrations-worker.config"
fi

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
