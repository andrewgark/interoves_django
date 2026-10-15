# Background index creation pattern

Use this pattern whenever you need to add a database index that could block
deploys on large tables (any table with >100k rows: `games_attempt`,
`games_hintattempt`, `games_registration`, etc.).

**Never run blocking DDL inside a Django migration** — a single-instance EB
deploy stops the old app before starting the new one, so a long `CREATE INDEX`
will cause downtime.

---

## The deployment-safe pieces

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
- Idempotent: verifies the exact column order for existing indexes and refuses
  to overwrite a same-name index with a different definition.
- DDL runs under the shared MySQL named lock in
  `games.management.commands._background_index_executor`.

Register the new `INDEX_DDL` list in `check_background_migrations.py` so
progress can be inspected:

```python
from games.management.commands.ensure_yourmodel_indexes import INDEX_DDL as INDEX_DDL_0NNN
...
_check_indexes(self, "0NNN – YourModel index", INDEX_DDL_0NNN, schema)
```

### 4. Green deploy executor

Do not add DDL or data backfills to an EB instance hook. The Green bundle
removes the legacy `02_background_migrations.sh`; `scripts/deploy_green.sh`
invokes `manage.py run_deploy_background_work` once after EB reports the new
version healthy. That command holds the global MySQL named lock, verifies
indexes by name and ordered columns, and performs missing online DDL
synchronously. It does not infer backfill completion from `ChainTaskState`
row presence because normal play creates only the current mode's row. The
historical backfill remains an explicit operation via
`run_deploy_background_work --include-chain-backfill` after a read-only scope
and completeness review. An executor error exits the deploy pipeline. The bundle builder writes
`interoves_eb_config_manifest.json` and fails if protected capacity, health, or
deployment configuration changes unexpectedly.

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

## Running manually (only through a controlled maintenance window)

```bash
./scripts/with_rds.sh manage.py run_deploy_background_work
```

The two `ensure_*_indexes` commands remain useful for inspection with
`--dry-run` / `--check-only`; execution also takes the shared database lock.
Never background these commands with `nohup`, `&`, or `disown`.
