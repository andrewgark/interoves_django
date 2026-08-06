#!/usr/bin/env bash
# Keep the newest N Elastic Beanstalk application versions; delete older ones
# (including source bundles in the EB S3 bucket).
#
# Usage:
#   ./scripts/cleanup_eb_app_versions.sh          # keep 20
#   ./scripts/cleanup_eb_app_versions.sh 30
#   KEEP=20 DRY_RUN=1 ./scripts/cleanup_eb_app_versions.sh
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
APP="${EB_APPLICATION_NAME:-interoves}"
KEEP="${1:-${KEEP:-20}}"
DRY_RUN="${DRY_RUN:-0}"

export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

if ! [[ "$KEEP" =~ ^[0-9]+$ ]] || [[ "$KEEP" -lt 1 ]]; then
  echo "KEEP must be a positive integer (got: $KEEP)" >&2
  exit 1
fi

echo "Application=$APP Region=$REGION Keep=$KEEP DryRun=$DRY_RUN"

mapfile -t VERSIONS < <(aws elasticbeanstalk describe-application-versions \
  --region "$REGION" --application-name "$APP" \
  --query 'sort_by(ApplicationVersions,&DateCreated) | reverse(@) | [].VersionLabel' \
  --output text | tr '\t' '\n')

TOTAL=${#VERSIONS[@]}
echo "Total versions: $TOTAL"

if [[ "$TOTAL" -le "$KEEP" ]]; then
  echo "Nothing to delete (already <= $KEEP)."
  exit 0
fi

TO_DELETE=("${VERSIONS[@]:$KEEP}")
echo "Will delete ${#TO_DELETE[@]} older version(s)."

deleted=0
failed=0
for label in "${TO_DELETE[@]}"; do
  [[ -z "$label" ]] && continue
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN delete: $label"
    continue
  fi
  if aws elasticbeanstalk delete-application-version \
    --region "$REGION" \
    --application-name "$APP" \
    --version-label "$label" \
    --delete-source-bundle; then
    deleted=$((deleted + 1))
    echo "deleted $label ($deleted/${#TO_DELETE[@]})"
  else
    failed=$((failed + 1))
    echo "FAILED $label (in use by an environment?)" >&2
  fi
done

echo "Done. deleted=$deleted failed=$failed"
