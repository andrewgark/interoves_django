#!/usr/bin/env bash
# Run with an identity that can edit IAM customer-managed policies (e.g. account root
# in AWS CloudShell, or a one-off admin profile). Not for the interoves IAM user.
set -euo pipefail
IAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
aws iam create-policy-version \
  --policy-arn arn:aws:iam::916000456640:policy/interoves-cli-access \
  --policy-document "file://${IAM_DIR}/interoves-cli-access-policy.json" \
  --set-as-default
aws iam create-policy-version \
  --policy-arn arn:aws:iam::916000456640:policy/ai-bot-runtime \
  --policy-document "file://${IAM_DIR}/ai-bot-runtime-policy.json" \
  --set-as-default
echo "OK: interoves-cli-access and ai-bot-runtime updated."
