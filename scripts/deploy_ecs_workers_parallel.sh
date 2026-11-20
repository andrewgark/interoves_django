#!/usr/bin/env bash
# Deploy independent non-browser ECS workers concurrently.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_URI="${1:-}"
shift || true

if [[ -z "$IMAGE_URI" ]]; then
    echo "Usage: $0 IMAGE_URI [--profile quiet|normal|game-day] [--mode ecs-fargate|ecs-fargate-spot] [--desired-count N] [--apply]" >&2
    exit 2
fi

workdir="$(mktemp -d /tmp/interoves-ecs-workers-parallel.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT

workers=(background identity recheck)
pids=()
logs=()
for worker in "${workers[@]}"; do
    log="$workdir/$worker.log"
    logs+=("$log")
    "$ROOT/scripts/deploy_ecs_worker.sh" "$worker" "$IMAGE_URI" "$@" >"$log" 2>&1 &
    pids+=("$!")
done

failed=0
for index in "${!workers[@]}"; do
    worker="${workers[$index]}"
    if wait "${pids[$index]}"; then
        printf '[%s] completed\n' "$worker"
    else
        printf '[%s] failed\n' "$worker" >&2
        failed=1
    fi
    sed "s/^/[$worker] /" "${logs[$index]}"
done

exit "$failed"
