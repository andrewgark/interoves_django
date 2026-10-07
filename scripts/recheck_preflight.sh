#!/usr/bin/env bash
# Validate the recheck worker bundle and, optionally, the live Green schema.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_SCHEMA=0
for arg in "$@"; do
    case "$arg" in
        --schema) CHECK_SCHEMA=1 ;;
        *) echo "Usage: $0 [--schema]" >&2; exit 2 ;;
    esac
done

PYTHON="${PYTHON:-$ROOT/../venv/interoves_django/bin/python}"
[[ -x "$PYTHON" ]] || { echo "Python executable not found: $PYTHON" >&2; exit 1; }

for required in \
    "$ROOT/games/management/commands/dispatch_recheck_outbox_loop.py" \
    "$ROOT/.platform/hooks/postdeploy/10_enable_word_salad_dispatcher.sh" \
    "$ROOT/games/migrations/0233_wordsaladrecheckjob_pending_resolution.py" \
    "$ROOT/games/migrations/0234_chaintaskstate_actor_key.py" \
    "$ROOT/games/migrations/0235_attempt_games_attempt_status_idx.py" \
    "$ROOT/games/migrations/0236_merge_legacy_chain_state_duplicates.py" \
    "$ROOT/games/migrations/0237_attempt_actor_scope_indexes.py"; do
    [[ -f "$required" ]] || { echo "Missing recheck prerequisite: $required" >&2; exit 1; }
done

(cd "$ROOT" && "$PYTHON" manage.py check)

if [[ "$CHECK_SCHEMA" == "1" ]]; then
    plan_file="$(mktemp /tmp/interoves-recheck-migrations.XXXXXX)"
    trap 'rm -f "$plan_file"' EXIT
    "$ROOT/scripts/with_rds.sh" manage.py showmigrations games --plan >"$plan_file"
    if grep -q '^\[ \]' "$plan_file"; then
        echo 'Refusing recheck-worker deploy: Green has unapplied games migrations:' >&2
        grep '^\[ \]' "$plan_file" >&2
        exit 1
    fi
fi

if [[ "$CHECK_SCHEMA" == "1" ]]; then
    echo 'Recheck preflight passed (including Green schema).'
else
    echo 'Recheck preflight passed.'
fi
