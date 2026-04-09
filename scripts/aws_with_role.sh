#!/usr/bin/env bash
# Run any command after loading secrets/aws.env and assuming INTEROVES_AWS_ROLE_ARN (if configured).
#
# Usage (from repo root):
#   ./scripts/aws_with_role.sh aws sts get-caller-identity
#   ./scripts/aws_with_role.sh eb status

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$REPO_ROOT"

exec "$@"
