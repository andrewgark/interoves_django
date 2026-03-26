# Background index creation pattern

Use this pattern whenever you need to add a database index that could block
deploys on large tables (any table with >100k rows: `games_attempt`,
`games_hintattempt`, `games_registration`, etc.).

**Never run blocking DDL inside a Django migration** — a single-instance EB
deploy stops the old app before starting the new one, so a long `CREATE INDEX`
will cause downtime.

---

## The four pieces

### 1. Migration — `SeparateDatabaseAndState`

Django records the index in its schema state (so `makemigrations` stays
consistent) but runs **no SQL**:

```python
# games/migrations/0NNN_your_index.py
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('games', '0NNN_previous')]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='yourmodel',
                    index=models.Index(
                        fields=['field_a', 'field_b'],
                        name='games_yourm_field_a_field_b_idx',
                    ),
                ),
            ],
            database_operations=[],   # ← no SQL on migrate
        ),
    ]
```

### 2. `Meta.indexes` in the model

Keep the model in sync with the migration state so Django doesn't complain:

```python
class YourModel(models.Model):
    ...
    class Meta:
        indexes = [
            models.Index(fields=['field_a', 'field_b'],
                         name='games_yourm_field_a_field_b_idx'),
        ]
```

### 3. Management command — `ensure_*_indexes.py`

Pattern: `games/management/commands/ensure_<table>_indexes.py`.
Copy `ensure_registration_indexes.py` as a template.  Key points:
- Define `INDEX_DDL` as a module-level list of `(table, index_name, ddl)` triples.
- DDL always includes `ALGORITHM=INPLACE LOCK=NONE` (InnoDB online DDL).
- `_index_exists()` checks `information_schema.statistics`.
- Supports `--dry-run` and `--check-only`.
- Idempotent: skips indexes that already exist (also catches MySQL errno 1061).

Register the new `INDEX_DDL` list in `check_background_migrations.py` so
progress can be inspected:

```python
from games.management.commands.ensure_yourmodel_indexes import INDEX_DDL as INDEX_DDL_0NNN
...
_check_indexes(self, "0NNN – YourModel index", INDEX_DDL_0NNN, schema)
```

### 4. Postdeploy hook — `02_background_migrations.sh`

Add a new array + loop in `.platform/hooks/postdeploy/02_background_migrations.sh`:

```bash
INDEXES_YOURMODEL=(
    "games_yourm_field_a_field_b_idx|CREATE INDEX games_yourm_field_a_field_b_idx ON games_yourmodel (field_a_id, field_b_id) ALGORITHM=INPLACE LOCK=NONE"
)

log "--- 0NNN: checking/creating YourModel indexes ---"
for entry in "${INDEXES_YOURMODEL[@]}"; do
    name="${entry%%|*}"
    sql="${entry#*|}"
    create_index_bg "$name" "$sql" "games_yourmodel"
done
```

`create_index_bg` checks existence first, then launches the DDL as a background
process (`& disown`) with its own log at `/var/log/app/idx_<name>.log`.

---

## Index naming convention

`{app}_{model[:5]}_{col1}_{col2}_idx`, e.g.:
- `games_attem_task_id_c73fd5_idx` — Attempt(task_id, team_id, time)
- `games_reg_game_team_idx` — Registration(game_id, team_id)

Use a short memorable suffix (or a 6-char hash if following Django's auto-naming).

---

## Checking progress

```bash
./scripts/with_rds.sh manage.py check_background_migrations
```

## Running manually (e.g. after the first deploy)

```bash
./scripts/with_rds.sh manage.py ensure_registration_indexes
./scripts/with_rds.sh manage.py ensure_games_0109_indexes
```
