#!/bin/bash
# Runs long-running DB work in the background AFTER Daphne is already up.
# This file is re-entrant: idempotency checks skip work that is already done.

set -euo pipefail

APP=/var/app/current
PYTHON=$(ls /var/app/venv/*/bin/python 2>/dev/null | head -1)
LOG=/var/log/app/background_migrations.log

mkdir -p /var/log/app

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" | tee -a "$LOG"; }

# t3.nano (~0.5 GiB) OOMs or hits ErrorCode 0000000001 during this hook; t3.small is fine.
# Idempotent DB work will run on a larger instance in the same env.
MEM_KB=$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${MEM_KB:-0}" -lt 900000 ]; then
  log "skip 02_background_migrations: MemTotal ${MEM_KB}kB < 900000 (need t3.small+)"
  exit 0
fi

# ---------------------------------------------------------------------------
# Helper: run a Python snippet via manage.py shell and return its stdout
# ---------------------------------------------------------------------------
pyq() {
    cd "$APP" && "$PYTHON" manage.py shell --no-startup -c "$1" 2>/dev/null
}

# ---------------------------------------------------------------------------
# 0109 — 8 compound indexes (ALGORITHM=INPLACE LOCK=NONE, non-blocking)
# ---------------------------------------------------------------------------

INDEXES_ATTEMPT=(
    "games_attem_task_id_c73fd5_idx|CREATE INDEX games_attem_task_id_c73fd5_idx ON games_attempt (task_id, team_id, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_attem_task_id_198c5d_idx|CREATE INDEX games_attem_task_id_198c5d_idx ON games_attempt (task_id, user_id, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_attem_task_id_85c642_idx|CREATE INDEX games_attem_task_id_85c642_idx ON games_attempt (task_id, anon_key, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_attem_task_id_bd72cc_idx|CREATE INDEX games_attem_task_id_bd72cc_idx ON games_attempt (task_id, status) ALGORITHM=INPLACE LOCK=NONE"
)

INDEXES_HINT=(
    "games_hinta_hint_id_7a53ab_idx|CREATE INDEX games_hinta_hint_id_7a53ab_idx ON games_hintattempt (hint_id, team_id, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_hinta_hint_id_dfdf44_idx|CREATE INDEX games_hinta_hint_id_dfdf44_idx ON games_hintattempt (hint_id, user_id, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_hinta_hint_id_c56ad5_idx|CREATE INDEX games_hinta_hint_id_c56ad5_idx ON games_hintattempt (hint_id, anon_key, time) ALGORITHM=INPLACE LOCK=NONE"
    "games_hinta_hint_id_a6f474_idx|CREATE INDEX games_hinta_hint_id_a6f474_idx ON games_hintattempt (hint_id, is_real_request) ALGORITHM=INPLACE LOCK=NONE"
)

index_exists() {
    local name="$1" table="$2"
    pyq "
from django.db import connection
with connection.cursor() as c:
    c.execute(\"SHOW INDEX FROM $table WHERE Key_name='$name'\")
    print('yes' if c.fetchone() else 'no')
"
}

create_index_bg() {
    local name="$1" sql="$2" table="$3"
    local ilog="/var/log/app/idx_${name}.log"
    if [ "$(index_exists "$name" "$table")" = "yes" ]; then
        log "  index $name already exists — skip"
        return
    fi
    log "  starting index $name in background"
    (
        cd "$APP"
        log_line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') creating $name"
        echo "$log_line" >> "$ilog"
        if "$PYTHON" manage.py shell --no-startup -c "
from django.db import connection
with connection.cursor() as c:
    c.execute('$sql')
" >> "$ilog" 2>&1; then
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $name DONE" | tee -a "$ilog" >> "$LOG"
        else
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $name FAILED" | tee -a "$ilog" >> "$LOG"
        fi
    ) &
    disown $!
    log "  $name launched (PID $!)"
}

INDEXES_REGISTRATION=(
    "games_reg_game_team_idx|CREATE INDEX games_reg_game_team_idx ON games_registration (game_id, team_id) ALGORITHM=INPLACE LOCK=NONE"
)

log "=== 02_background_migrations start ==="

log "--- 0109: checking/creating indexes ---"
for entry in "${INDEXES_ATTEMPT[@]}"; do
    name="${entry%%|*}"
    sql="${entry#*|}"
    create_index_bg "$name" "$sql" "games_attempt"
done
for entry in "${INDEXES_HINT[@]}"; do
    name="${entry%%|*}"
    sql="${entry#*|}"
    create_index_bg "$name" "$sql" "games_hintattempt"
done

# ---------------------------------------------------------------------------
# 0112 — composite index on games_registration(game_id, team_id)
# ---------------------------------------------------------------------------
log "--- 0112: checking/creating Registration index ---"
for entry in "${INDEXES_REGISTRATION[@]}"; do
    name="${entry%%|*}"
    sql="${entry#*|}"
    create_index_bg "$name" "$sql" "games_registration"
done

# ---------------------------------------------------------------------------
# 0111 — backfill ChainTaskState rows
# ---------------------------------------------------------------------------
log "--- 0111: checking backfill ---"

HAS_DATA=$(pyq "
from games.models import ChainTaskState
print('yes' if ChainTaskState.objects.exists() else 'no')
")

if [ "$HAS_DATA" = "yes" ]; then
    log "  ChainTaskState already populated — skip"
else
    log "  starting backfill_chain_task_states in background"
    (
        cd "$APP"
        echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') backfill start" >> /var/log/app/backfill_chain.log
        if "$PYTHON" manage.py backfill_chain_task_states >> /var/log/app/backfill_chain.log 2>&1; then
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') backfill DONE" | tee -a /var/log/app/backfill_chain.log >> "$LOG"
        else
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') backfill FAILED" | tee -a /var/log/app/backfill_chain.log >> "$LOG"
        fi
    ) &
    disown $!
    log "  backfill launched (PID $!)"
fi

log "=== 02_background_migrations done (background jobs running) ==="
