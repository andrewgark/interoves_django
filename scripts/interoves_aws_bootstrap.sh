#!/usr/bin/env bash
# Source from repo scripts: loads secrets/aws.env and optionally assumes INTEROVES_AWS_ROLE_ARN.
# shellcheck shell=bash

interoves_aws_bootstrap() {
    local root aws_env id out session role_name

    root="${1:-}"
    [[ -n "$root" ]] || root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    aws_env="${root}/secrets/aws.env"
    [[ -f "$aws_env" ]] || return 0

    set -a
    # shellcheck disable=SC1091
    source "$aws_env"
    set +a

    [[ -n "${INTEROVES_AWS_ROLE_ARN:-}" ]] || return 0

    id=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null) || id=""
    role_name="${INTEROVES_AWS_ROLE_ARN##*/}"
    if [[ -n "$id" ]] && [[ "$id" == *"assumed-role/${role_name}/"* ]]; then
        return 0
    fi

    session="interoves-$(hostname -s 2>/dev/null || echo host)-$$"
    if ! out=$(aws sts assume-role \
        --role-arn "$INTEROVES_AWS_ROLE_ARN" \
        --role-session-name "$session" \
        --duration-seconds 3600 \
        --output json 2>/dev/null); then
        echo "Warning: could not assume ${INTEROVES_AWS_ROLE_ARN} (base credentials or trust policy). Using current AWS identity." >&2
        return 0
    fi

    export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
    AWS_ACCESS_KEY_ID=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
    AWS_SECRET_ACCESS_KEY=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
    AWS_SESSION_TOKEN=$(echo "$out" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SessionToken'])")
    unset AWS_PROFILE
    export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
}
