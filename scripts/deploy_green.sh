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
RELEASE_PROFILE="full"
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

if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]; then
    [[ -n "${PREBUILT_RELEASE_SHA256:-}" && "${PREBUILT_RELEASE_SHA256}" =~ ^[0-9a-fA-F]{64}$ ]] || {
        echo "Exact-artifact mode requires PREBUILT_RELEASE_SHA256 (64 hex characters)." >&2
        exit 2
    }
    [[ -n "${DEPLOY_SOURCE_COMMIT:-}" && "${DEPLOY_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || {
        echo "Exact-artifact mode requires the full DEPLOY_SOURCE_COMMIT." >&2
        exit 2
    }
    [[ "${ENABLE_POSTDEPLOY_SCHEMA_WORK:-0}" == "0" ]] || {
        echo "Exact-artifact release requires ENABLE_POSTDEPLOY_SCHEMA_WORK=0." >&2
        exit 2
    }
    [[ -f "$PREBUILT_RELEASE_ARTIFACT" ]] || {
        echo "Prebuilt release artifact not found: $PREBUILT_RELEASE_ARTIFACT" >&2
        exit 2
    }
    PROJECT_PYTHON="${PROJECT_PYTHON:-$ROOT/../venv/interoves_django/bin/python}"
    "$PROJECT_PYTHON" "$ROOT/scripts/offline_release_artifact.py" verify \
        "$PREBUILT_RELEASE_ARTIFACT" --source-commit "$DEPLOY_SOURCE_COMMIT" \
        --sha256 "$PREBUILT_RELEASE_SHA256"
    RELEASE_PROFILE="$(unzip -p "$PREBUILT_RELEASE_ARTIFACT" release-manifest.json | \
        "$PROJECT_PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("release_profile", "full"))')"
    [[ "$RELEASE_PROFILE" == "full" || "$RELEASE_PROFILE" == "observe-only" ]] || {
        echo "Unknown prebuilt release profile: $RELEASE_PROFILE" >&2
        exit 2
    }
    "$PROJECT_PYTHON" "$ROOT/scripts/eb_config_manifest.py" verify-zip \
        "$PREBUILT_RELEASE_ARTIFACT" "$ENV_NAME" "$DEPLOY_SOURCE_COMMIT"
fi

if [[ "$DO_DEPLOY" == "1" ]]; then
    echo "Target: $ENV_NAME (production Green ALB)"
    "$ROOT/scripts/deploy_preflight.sh"
else
    echo "Dry run: target would be $ENV_NAME (no AWS mutation)"
fi

workdir="$(mktemp -d /tmp/interoves-green-deploy.XXXXXX)"
source "$ROOT/scripts/deploy_gate_cleanup.sh"
deploy_gate_cleanup_install "$ROOT" "$workdir"
bundle="$workdir/interoves-green.zip"
release_source="${DEPLOY_SOURCE_DIR:-$ROOT}"
if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]; then
    cp "$PREBUILT_RELEASE_ARTIFACT" "$bundle"
    release_source=""
else
    "$ROOT/scripts/prepare_eb_bundle.sh" "$ENV_NAME" "$bundle" 1
fi

if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]; then
    sha="$(unzip -p "$bundle" interoves_django/deploy_version.txt)"
else
    sha="${DEPLOY_SOURCE_SHA:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)}"
fi
label="app-green-${sha}-$(date -u +%Y%m%d%H%M%S)"
if [[ "$DO_DEPLOY" == "0" ]]; then
    echo "Prepared version label: $label"
    exit 0
fi

if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]; then
    release_source="$workdir/release-source"
    mkdir -p "$release_source"
    unzip -q "$bundle" -d "$release_source"
fi

# Green never runs migrate on its instances, and the preflight above can only
# see migrations of the code already serving. Plan the release's own
# migrations through an RDS tunnel and refuse ones that would break old code.
release_migrations() {
    local secret_file="$ROOT/secrets/django_secret_key.txt"
    if [[ -z "${DJANGO_SECRET_KEY:-}" && ! -f "$release_source/secrets/django_secret_key.txt" && -f "$secret_file" ]]; then
        DJANGO_SECRET_KEY="$(<"$secret_file")"
        export DJANGO_SECRET_KEY
    fi
    PYTHON="${PYTHON:-$ROOT/../venv/interoves_django/bin/python}" \
        "$ROOT/scripts/with_rds.sh" --raw bash -c \
        'cd "$1" && shift && exec "$PYTHON" manage.py apply_release_migrations "$@"' \
        release-migrations "$release_source" "$@"
}
echo "Checking the release's own migrations against production"
release_migrations

source "$ROOT/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$ROOT"
account_id=$("$ROOT/scripts/aws_with_role.sh" aws sts get-caller-identity --query Account --output text)
bucket="elasticbeanstalk-${REGION}-${account_id}"
key="interoves/green/${label}.zip"
"$ROOT/scripts/aws_with_role.sh" aws s3 cp "$bundle" "s3://${bucket}/${key}" --region "$REGION" --only-show-errors
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk create-application-version \
    --region "$REGION" --application-name "$APP" --version-label "$label" \
    --source-bundle S3Bucket="$bucket",S3Key="$key" >/dev/null

# Publish assets under an immutable release prefix before switching any web
# instance. Old instances and cached HTML keep using their own prefix.
release_static_id="$(cd "$release_source" && bash scripts/static_source_fingerprint.sh)"
bundle_static_id="$(unzip -p "$bundle" interoves_django/static_release_id.txt 2>/dev/null || true)"
if [[ "$bundle_static_id" != "$release_static_id" ]]; then
    echo "Static source fingerprint in the EB bundle does not match its assets." >&2
    exit 1
fi
if [[ "$RELEASE_PROFILE" == "observe-only" ]]; then
    STATIC_RELEASE_ID="$release_static_id" DEPLOY_SOURCE_DIR="$release_source" \
        "$ROOT/scripts/publish_static.sh" --verify-only
    echo "Observe-only artifact has no frontend release; skipping static publication."
else
    STATIC_RELEASE_ID="$release_static_id" DEPLOY_SOURCE_DIR="$release_source" \
        "$ROOT/scripts/publish_static.sh"
fi

# Expanding migrations go in before any instance runs code that needs them.
echo "Applying release migrations before switching code"
release_migrations --apply

# A failed enable/update/wait/smoke must not strand the refresh gate. Arm
# cleanup before enabling so even a partially failed enable is followed by a
# best-effort delete; the Redis TTL remains the final fallback.
deploy_gate_cleanup_arm
"$ROOT/scripts/deploy_refresh_gate.sh" enable
deploy_gate_cleanup_update_requested
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk update-environment \
    --region "$REGION" --application-name "$APP" --environment-name "$ENV_NAME" \
    --version-label "$label" >/dev/null
"$ROOT/scripts/wait_for_eb_deployment.sh" "$ENV_NAME" "$label" "$REGION"
deploy_gate_cleanup_backend_ready
# DDL remains opt-in and is disabled for normal application releases. The
# schema-work command is deliberately outside the default deploy path.
case "${ENABLE_POSTDEPLOY_SCHEMA_WORK:-0}" in
    0) echo "Skipping deferred schema work (ENABLE_POSTDEPLOY_SCHEMA_WORK=0)." ;;
    1)
        echo "Running explicitly enabled serialized schema work on one healthy Green instance"
        "$ROOT/scripts/eb_run.sh" manage.py run_deploy_background_work
        ;;
    *) echo "ENABLE_POSTDEPLOY_SCHEMA_WORK must be 0 or 1." >&2; exit 2 ;;
esac
"$ROOT/scripts/smoke_prod_pages.sh"
"$ROOT/scripts/deploy_refresh_gate.sh" disable
deploy_gate_cleanup_disarm
echo "Green deploy complete: $label"
