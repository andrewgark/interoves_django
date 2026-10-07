#!/usr/bin/env bash
# Production deploy guardrails for the active Green ALB environment.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAN_FILE="$(mktemp /tmp/interoves-migrate-plan.XXXXXX)"
trap 'rm -f "$PLAN_FILE"' EXIT

echo "Running Django production checks"
# Run inside Green so check --deploy sees the same environment settings as the
# live application, rather than local DEBUG/security defaults with a DB tunnel.
"$ROOT/scripts/eb_run.sh" manage.py check --deploy

echo "Checking production migration state"
if ! "$ROOT/scripts/with_rds.sh" manage.py migrate --plan >"$PLAN_FILE" 2>&1; then
    cat "$PLAN_FILE" >&2
    echo "Deploy preflight failed: migrate --plan could not complete." >&2
    exit 1
fi

if ! rg -q "No planned migration operations\." "$PLAN_FILE"; then
    cat "$PLAN_FILE" >&2
    echo "Deploy preflight failed: production has unapplied migrations." >&2
    echo "Apply the migrations first, then retry the deploy." >&2
    exit 1
fi

echo "Deploy preflight passed: schema is current."
