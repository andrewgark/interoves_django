#!/usr/bin/env bash
# Run a command on the live EB instance using EC2 Instance Connect.
#
# Auth: IAM only — generates a throw-away SSH key, pushes the public half to
# the instance for 60 s via aws ec2-instance-connect, then SSHes in.
# No static key file, no security group changes, no SSM agent needed.
#
# Usage (from repo root):
#   ./scripts/eb_run.sh manage.py check_background_migrations
#   ./scripts/eb_run.sh manage.py migrate --plan
#   ./scripts/eb_run.sh manage.py shell
#   ./scripts/eb_run.sh --raw "cat /var/log/app/background_migrations.log"
#   ./scripts/eb_run.sh --raw "tail -f /var/log/web.stdout.log"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/interoves_aws_bootstrap.sh"
interoves_aws_bootstrap "$REPO_ROOT"

REGION="eu-central-1"
ENV_NAME="interoves-env"
OS_USER="ec2-user"
APP_DIR="/var/app/current"
KEY_FILE="$(mktemp -u /tmp/eb_ic_XXXXXX)"

# ---- Parse flags ------------------------------------------------------------
RAW=0
if [[ "${1:-}" == "--raw" ]]; then
    RAW=1
    shift
fi

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 [--raw] manage.py <command> [args...]" >&2
    exit 1
fi

# ---- Resolve instance -------------------------------------------------------
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:elasticbeanstalk:environment-name,Values=${ENV_NAME}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
INSTANCE_IP=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo "Instance: ${INSTANCE_ID} (${INSTANCE_IP})"

# ---- Generate a throw-away key pair -----------------------------------------
cleanup() { rm -f "$KEY_FILE" "${KEY_FILE}.pub"; }
trap cleanup EXIT

ssh-keygen -t rsa -b 2048 -f "$KEY_FILE" -N "" -q

# ---- Push public key (valid for 60 s) ---------------------------------------
aws ec2-instance-connect send-ssh-public-key \
    --region "$REGION" \
    --instance-id "$INSTANCE_ID" \
    --instance-os-user "$OS_USER" \
    --ssh-public-key "file://${KEY_FILE}.pub" > /dev/null \
    && echo "SSH key pushed (60 s window)" \
    || { echo "Failed to push SSH key" >&2; exit 1; }

# ---- SSH helper (no host-key prompts) ---------------------------------------
SSH="ssh -i ${KEY_FILE} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 ${OS_USER}@${INSTANCE_IP}"

# ---- Build and run remote command -------------------------------------------
# Pipe a Python script over stdin so we never need to escape arguments for bash.
# The script reads env vars from the running Daphne process's /proc entry
# (avoids parsing the EB env file, which has unquoted special chars).

ARGS_B64=$(python3 -c "import base64, json, sys; print(base64.b64encode(json.dumps(sys.argv[1:]).encode()).decode())" -- "$@")

if [[ $RAW -eq 1 ]]; then
    # Raw mode: run a shell command with the EB env injected
    RAW_CMD="$*"
    RAW_CMD_B64=$(python3 -c "import base64, sys; print(base64.b64encode(sys.argv[1].encode()).decode())" -- "$RAW_CMD")
    $SSH "sudo python3" <<PYEOF
import base64, os, subprocess, sys
pid = subprocess.check_output(['pgrep','-of','daphne'], text=True).strip()
env = dict(os.environ)
if pid:
    with open(f'/proc/{pid}/environ') as f:
        for kv in f.read().split(chr(0)):
            if '=' in kv:
                k, _, v = kv.partition('='); env[k] = v
raw_cmd = base64.b64decode('${RAW_CMD_B64}').decode()
sys.exit(subprocess.call(['bash', '-c', raw_cmd], env=env))
PYEOF
else
    # manage.py mode: discover venv Python and run manage.py with prod env
    $SSH "sudo python3" <<PYEOF
import base64, glob, json, os, subprocess, sys
args = json.loads(base64.b64decode('${ARGS_B64}').decode())
pid = subprocess.check_output(['pgrep','-of','daphne'], text=True).strip()
env = dict(os.environ)
if pid:
    with open(f'/proc/{pid}/environ') as f:
        for kv in f.read().split(chr(0)):
            if '=' in kv:
                k, _, v = kv.partition('='); env[k] = v
os.chdir('${APP_DIR}')
python = sorted(glob.glob('/var/app/venv/*/bin/python'))[-1]
sys.exit(subprocess.call([python] + args, env=env))
PYEOF
fi
