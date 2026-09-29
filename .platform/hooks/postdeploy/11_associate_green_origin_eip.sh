#!/usr/bin/env bash
set -euo pipefail

# Keep the public origin stable when Elastic Beanstalk replaces the Green
# instance.  The EIP is intentionally specific to Green and is not used by
# Blue rollback or workers.
EIP_ALLOCATION_ID="eipalloc-04fce5f757cc4bedd"
METADATA_TOKEN="$(curl -fsS -X PUT \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600' \
    http://169.254.169.254/latest/api/token)"
INSTANCE_ID="$(curl -fsS \
    -H "X-aws-ec2-metadata-token: ${METADATA_TOKEN}" \
    http://169.254.169.254/latest/meta-data/instance-id)"
REGION="$(curl -fsS \
    -H "X-aws-ec2-metadata-token: ${METADATA_TOKEN}" \
    http://169.254.169.254/latest/meta-data/placement/region)"

/usr/bin/aws ec2 associate-address \
    --region "${REGION}" \
    --allocation-id "${EIP_ALLOCATION_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --allow-reassociation >/dev/null

echo "Associated Green origin EIP ${EIP_ALLOCATION_ID} with ${INSTANCE_ID}"
