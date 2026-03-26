"""
Report the progress of background DB work that was deferred from migrations
0109 (compound indexes), 0111 (ChainTaskState backfill), and 0112 (Registration index).

Usage:
    python manage.py check_background_migrations
"""

from django.core.management.base import BaseCommand
from django.db import connection

from games.management.commands.ensure_games_0109_indexes import INDEX_DDL as INDEX_DDL_0109
from games.management.commands.ensure_registration_indexes import INDEX_DDL as INDEX_DDL_0112
from games.models import CHAIN_TASK_TYPES, Attempt, ChainTaskState


def _check_indexes(command, label, index_ddl, schema):
    command.stdout.write(f"=== {label} ===")
    if connection.vendor != "mysql":
        command.stdout.write(command.style.WARNING("  (non-MySQL: index check skipped)"))
        return
    present, missing = [], []
    with connection.cursor() as cursor:
        for table, idx_name, _ddl in index_ddl:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.statistics
                WHERE table_schema = %s AND table_name = %s AND index_name = %s
                LIMIT 1
                """,
                [schema, table, idx_name],
            )
            (present if cursor.fetchone() else missing).append((table, idx_name))
    for table, name in present:
        command.stdout.write(command.style.SUCCESS(f"  OK      {table}.{name}"))
    for table, name in missing:
        command.stdout.write(command.style.WARNING(f"  MISSING {table}.{name}"))
    command.stdout.write(
        f"  {len(present)}/{len(index_ddl)} indexes present"
        + (" — all done!" if not missing else f" — {len(missing)} still pending")
    )


class Command(BaseCommand):
    help = "Check progress of background migrations 0109 (indexes), 0111 (backfill), 0112 (Registration index)."

    def handle(self, *args, **options):
        schema = connection.settings_dict.get("NAME", "")
        _check_indexes(self, "0109 – Attempt/HintAttempt compound indexes", INDEX_DDL_0109, schema)
        self.stdout.write("")
        _check_indexes(self, "0112 – Registration(game, team) index", INDEX_DDL_0112, schema)

        self.stdout.write("")
        self.stdout.write("=== 0111 – ChainTaskState backfill ===")
        chain_count = ChainTaskState.objects.count()
        combos_count = (
            Attempt.manager.filter(task__task_type__in=CHAIN_TASK_TYPES)
            .values("team_id", "user_id", "anon_key", "task_id")
            .distinct()
            .count()
        )
        self.stdout.write(f"  ChainTaskState rows : {chain_count:,}")
        self.stdout.write(f"  Expected (combos)   : {combos_count:,}")
        if chain_count >= combos_count:
            self.stdout.write(self.style.SUCCESS("  Status: done (or nothing to backfill)"))
        elif chain_count == 0:
            self.stdout.write(self.style.WARNING("  Status: not started yet"))
        else:
            pct = 100 * chain_count / combos_count if combos_count else 0
            self.stdout.write(
                self.style.WARNING(f"  Status: in progress ({pct:.1f}%)")
            )
