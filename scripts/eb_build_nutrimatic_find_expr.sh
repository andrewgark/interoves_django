#!/usr/bin/env bash
# Optional / last-resort: build find-expr ON THE LIVE EB INSTANCE (competes with prod CPU/RAM/disk).
#
# Preferred: ./scripts/stage_nutrimatic_find_expr_docker_al2023.sh on your laptop or CI (amazonlinux:2023
# in Docker), then deploy — same glibc as EB without touching the instance.
#
# This script syncs minimal nutrimatic sources to the EB instance and runs
# scripts/build_nutrimatic_find_expr_amazonlinux2023_native.sh with INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB=1.
#
# Requires: same IAM patterns as scripts/eb_run.sh (EC2 Instance Connect + SSH), rsync, tar.
# First Conan build on the instance can take 30–60+ minutes.
#
# Usage (from repo root):
#   ./scripts/eb_build_nutrimatic_find_expr.sh
#   NUTRIMATIC_SRC=/path/to/nutrimatic-ru ./scripts/eb_build_nutrimatic_find_expr.sh
#
# If the first run uploaded sources but SSH dropped mid-build, reuse ~/nutrimatic-ru-src on the instance:
#   SKIP_UPLOAD=1 ./scripts/eb_build_nutrimatic_find_expr.sh
#
# Optional: SSH_CONNECT_TIMEOUT=90 (seconds) if you see "Connection timed out during banner exchange".

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$REPO_ROOT"

REGION="eu-central-1"
ENV_NAME="interoves-env"
OS_USER="ec2-user"
NUTRIMATIC_SRC="${NUTRIMATIC_SRC:-$HOME/nutrimatic-ru}"
REMOTE_DIR="nutrimatic-ru-src"
NATIVE_SCRIPT="${REPO_ROOT}/scripts/build_nutrimatic_find_expr_amazonlinux2023_native.sh"
SKIP_UPLOAD="${SKIP_UPLOAD:-0}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-60}"

if [[ "$SKIP_UPLOAD" != 1 ]] && [[ ! -f "$NUTRIMATIC_SRC/conanfile.py" ]]; then
  echo "NUTRIMATIC_SRC must contain conanfile.py: $NUTRIMATIC_SRC" >&2
  exit 1
fi
if [[ ! -f "$NATIVE_SCRIPT" ]]; then
  echo "Missing $NATIVE_SCRIPT" >&2
  exit 1
fi

INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:elasticbeanstalk:environment-name,Values=${ENV_NAME}" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
INSTANCE_IP=$(aws ec2 describe-instances --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
  echo "No running instance for $ENV_NAME" >&2
  exit 1
fi

echo "Instance: ${INSTANCE_ID} (${INSTANCE_IP})"

KEY_FILE="$(mktemp -u /tmp/eb_nut_ic_XXXXXX)"
TMP_TAR=""
if [[ "$SKIP_UPLOAD" != 1 ]]; then
  TMP_TAR="$(mktemp /tmp/nutrimatic-src.XXXXXX.tar.gz)"
fi
cleanup() { rm -f "$KEY_FILE" "${KEY_FILE}.pub"; [[ -n "$TMP_TAR" ]] && rm -f "$TMP_TAR"; }
trap cleanup EXIT

ssh-keygen -t rsa -b 2048 -f "$KEY_FILE" -N "" -q

push_ssh_key() {
  aws ec2-instance-connect send-ssh-public-key \
    --region "$REGION" \
    --instance-id "$INSTANCE_ID" \
    --instance-os-user "$OS_USER" \
    --ssh-public-key "file://${KEY_FILE}.pub" >/dev/null
  echo "SSH key pushed (use within ~60s for new connections)"
}

SSH_BASE=(ssh -i "${KEY_FILE}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT}")
SCP_BASE=(scp -i "${KEY_FILE}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT}")

if [[ "$SKIP_UPLOAD" != 1 ]]; then
  echo "=== Packing sources (no .git, build, indexes, dumps) ==="
  (
    cd "$NUTRIMATIC_SRC"
    _extra=()
    [[ -d web_static ]] && _extra+=(web_static)
    tar czf "$TMP_TAR" conanfile.py source cgi_scripts "${_extra[@]}"
  )

  echo "=== Uploading tarball ==="
  push_ssh_key
  "${SSH_BASE[@]}" "${OS_USER}@${INSTANCE_IP}" "rm -rf ~/${REMOTE_DIR} && mkdir -p ~/${REMOTE_DIR}"
  push_ssh_key
  "${SCP_BASE[@]}" "$TMP_TAR" "${OS_USER}@${INSTANCE_IP}:nutrimatic-upload.tgz"
  push_ssh_key
  "${SSH_BASE[@]}" "${OS_USER}@${INSTANCE_IP}" "tar xzf nutrimatic-upload.tgz -C ~/${REMOTE_DIR} && rm -f nutrimatic-upload.tgz"
else
  echo "=== SKIP_UPLOAD=1: not re-uploading tarball (expect ~/${REMOTE_DIR} from a prior run) ==="
  push_ssh_key
  "${SSH_BASE[@]}" "${OS_USER}@${INSTANCE_IP}" "test -f ~/${REMOTE_DIR}/conanfile.py" || {
    echo "Remote ~/${REMOTE_DIR}/conanfile.py missing. Run once without SKIP_UPLOAD=1." >&2
    exit 1
  }
fi

echo "=== Upload build script ==="
push_ssh_key
"${SCP_BASE[@]}" "$NATIVE_SCRIPT" "${OS_USER}@${INSTANCE_IP}:build_nutrimatic.sh"
"${SSH_BASE[@]}" "${OS_USER}@${INSTANCE_IP}" "chmod +x build_nutrimatic.sh"

echo "=== Build on instance (long) ==="
push_ssh_key
"${SSH_BASE[@]}" -t "${OS_USER}@${INSTANCE_IP}" \
  "INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB=1 ./build_nutrimatic.sh ~/${REMOTE_DIR}"

echo "Done. Verify: curl -sS -o /dev/null -w '%{http_code}' https://interoves.com/nutrimatic-ru/"
