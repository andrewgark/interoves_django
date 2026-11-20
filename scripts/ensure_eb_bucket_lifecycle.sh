#!/usr/bin/env bash
# Idempotent S3 lifecycle on the regional Elastic Beanstalk bucket:
# expire objects under interoves/ after DAYS (default 60).
#
# Usage:
#   ./scripts/ensure_eb_bucket_lifecycle.sh
#   DAYS=90 ./scripts/ensure_eb_bucket_lifecycle.sh
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
ACCOUNT="${AWS_ACCOUNT_ID:-916000456640}"
BUCKET="${EB_S3_BUCKET:-elasticbeanstalk-${REGION}-${ACCOUNT}}"
DAYS="${DAYS:-60}"
RULE_ID="interoves-app-versions-expire"

export AWS_PROFILE="${AWS_PROFILE:-interoves}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN 2>/dev/null || true

if ! [[ "$DAYS" =~ ^[0-9]+$ ]] || [[ "$DAYS" -lt 1 ]]; then
  echo "DAYS must be a positive integer (got: $DAYS)" >&2
  exit 1
fi

echo "Bucket=$BUCKET Rule=$RULE_ID ExpireAfterDays=$DAYS"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# Merge with existing rules (replace ours by ID, keep others).
EXISTING=$(aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" --region "$REGION" 2>/dev/null || echo '{"Rules":[]}')

python3 - "$EXISTING" "$RULE_ID" "$DAYS" "$TMP" <<'PY'
import json, sys
existing = json.loads(sys.argv[1])
rule_id, days, out_path = sys.argv[2], int(sys.argv[3]), sys.argv[4]
rules = [r for r in existing.get("Rules", []) if r.get("ID") != rule_id]
rules.append({
    "ID": rule_id,
    "Status": "Enabled",
    "Filter": {"Prefix": "interoves/"},
    "Expiration": {"Days": days},
    "NoncurrentVersionExpiration": {"NoncurrentDays": days},
})
# PutBucketLifecycleConfiguration expects Rules with ID/Status/Filter or Prefix.
cfg = {"Rules": rules}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f)
print(json.dumps(cfg, indent=2))
PY

aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --lifecycle-configuration "file://$TMP"

echo "Lifecycle applied."
aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" --region "$REGION" \
  --query 'Rules[].[ID,Status,Filter.Prefix,Expiration.Days]' --output table
