#!/usr/bin/env bash
# Wait for a specific EB application version and stable environment health.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="${1:-}"
EXPECTED_VERSION="${2:-}"
REGION="${3:-${AWS_DEFAULT_REGION:-eu-central-1}}"
TARGET_KIND="${4:-web}"
TIMEOUT_SECONDS="${EB_DEPLOY_TIMEOUT_SECONDS:-1800}"
POLL_SECONDS="${EB_DEPLOY_POLL_SECONDS:-15}"

if [[ -z "$ENV_NAME" || -z "$EXPECTED_VERSION" ]]; then
    echo "Usage: $0 ENVIRONMENT VERSION_LABEL [REGION] [web|worker]" >&2
    exit 2
fi
if [[ "$TARGET_KIND" != "web" && "$TARGET_KIND" != "worker" ]]; then
    echo "Target kind must be web or worker." >&2
    exit 2
fi
if ! [[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ && "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    echo "EB_DEPLOY_TIMEOUT_SECONDS and EB_DEPLOY_POLL_SECONDS must be positive integers." >&2
    exit 2
fi

if [[ "$TARGET_KIND" == "worker" ]]; then
    echo "Waiting for worker $ENV_NAME to serve $EXPECTED_VERSION and become Ready (Green/Ok or Grey/No Data)."
else
    echo "Waiting for $ENV_NAME to serve $EXPECTED_VERSION and become Green/Ok."
fi
started_at=$SECONDS
wrong_version_since=""
last_state="unavailable"

while true; do
    if ! state="$("$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-environments \
        --region "$REGION" --application-name interoves --environment-names "$ENV_NAME" \
        --query 'Environments[0].[Status,Health,HealthStatus,VersionLabel]' --output text)"; then
        echo "Could not read Elastic Beanstalk status: $state" >&2
        exit 1
    fi
    read -r status health health_status version <<< "$state"
    last_state="$state"

    if [[ "$status" == "Ready" ]]; then
        if [[ "$version" == "$EXPECTED_VERSION" ]]; then
            wrong_version_since=""
            if [[ "$health" == "Green" && "$health_status" == "Ok" ]] || \
                [[ "$TARGET_KIND" == "worker" && "$health" == "Grey" && "$health_status" == "No Data" ]]; then
                echo "EB deployment ready: $ENV_NAME / $EXPECTED_VERSION ($health/$health_status)."
                exit 0
            fi
        else
            if [[ -z "$wrong_version_since" ]]; then
                wrong_version_since=$SECONDS
            elif (( SECONDS - wrong_version_since >= 300 )); then
                echo "Environment is Ready on unexpected version '$version' (expected '$EXPECTED_VERSION')." >&2
                break
            fi
        fi
    else
        wrong_version_since=""
    fi

    if (( SECONDS - started_at >= TIMEOUT_SECONDS )); then
        echo "Timed out after ${TIMEOUT_SECONDS}s waiting for EB deployment. Last status: $last_state" >&2
        break
    fi
    sleep "$POLL_SECONDS"
done

echo "Recent Elastic Beanstalk events for $ENV_NAME:" >&2
"$ROOT/scripts/aws_with_role.sh" aws elasticbeanstalk describe-events \
    --region "$REGION" --environment-name "$ENV_NAME" --max-records 10 \
    --query 'Events[].{Time:EventDate,Severity:Severity,Message:Message}' --output table >&2 || true
exit 1
