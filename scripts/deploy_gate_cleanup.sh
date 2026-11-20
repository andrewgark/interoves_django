#!/usr/bin/env bash
# Shared EXIT/signal cleanup for the Redis hard-refresh gate during Green deploys.

deploy_gate_cleanup_install() {
    DEPLOY_CLEANUP_ROOT="$1"
    DEPLOY_CLEANUP_WORKDIR="$2"
    DEPLOY_CLEANUP_GATE_ARMED=0
    DEPLOY_CLEANUP_UPDATE_REQUESTED=0
    DEPLOY_CLEANUP_BACKEND_READY=0
    trap 'deploy_gate_cleanup_run "$?"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

deploy_gate_cleanup_arm() {
    DEPLOY_CLEANUP_GATE_ARMED=1
}

deploy_gate_cleanup_backend_ready() {
    DEPLOY_CLEANUP_BACKEND_READY=1
}

deploy_gate_cleanup_update_requested() {
    DEPLOY_CLEANUP_UPDATE_REQUESTED=1
}

deploy_gate_cleanup_disarm() {
    DEPLOY_CLEANUP_GATE_ARMED=0
}

deploy_gate_cleanup_run() {
    local status="${1:-0}"
    trap - EXIT HUP INT TERM

    if [[ "${DEPLOY_CLEANUP_GATE_ARMED:-0}" == "1" ]]; then
        if "$DEPLOY_CLEANUP_ROOT/scripts/deploy_refresh_gate.sh" disable; then
            DEPLOY_CLEANUP_GATE_ARMED=0
        else
            echo "WARNING: could not clear Redis deploy-refresh gate; its 3600-second TTL is the fallback." >&2
            if [[ "$status" == "0" ]]; then
                status=1
            fi
        fi
    fi

    if [[ "$status" != "0" && "${DEPLOY_CLEANUP_BACKEND_READY:-0}" == "1" ]]; then
        echo "PARTIAL DEPLOY: EB backend rollout succeeded, but a later deploy step failed (status=$status)." >&2
        echo "The application is already on the new version; inspect static publication/smoke results before retrying." >&2
    elif [[ "$status" != "0" && "${DEPLOY_CLEANUP_UPDATE_REQUESTED:-0}" == "1" ]]; then
        echo "DEPLOY STATUS UNCERTAIN: EB update was requested, but completion was not confirmed (status=$status)." >&2
        echo "Inspect the Green environment version and health before retrying." >&2
    fi

    if [[ -n "${DEPLOY_CLEANUP_WORKDIR:-}" ]]; then
        rm -rf "$DEPLOY_CLEANUP_WORKDIR"
    fi
    exit "$status"
}
