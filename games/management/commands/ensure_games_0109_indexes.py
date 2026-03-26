"""
Create games.0109 composite indexes on MySQL when missing (same DDL as migration 0109).

Run from a host that can reach RDS with RDS_* env vars set (same as production settings),
or any MySQL default DATABASE_URL pattern used by this project.

Examples:
  python manage.py ensure_games_0109_indexes --dry-run
  python manage.py ensure_games_0109_indexes
  nohup python manage.py ensure_games_0109_indexes > /tmp/ensure_0109.log 2>&1 &
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


# Mirrors games.migrations.0109 MySQL forwards (table, index_name, full CREATE INDEX ...).
INDEX_DDL = [
    (
        "games_attempt",
        "games_attem_task_id_c73fd5_idx",
        "CREATE INDEX games_attem_task_id_c73fd5_idx ON games_attempt "
        "(task_id, team_id, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_attempt",
        "games_attem_task_id_198c5d_idx",
        "CREATE INDEX games_attem_task_id_198c5d_idx ON games_attempt "
        "(task_id, user_id, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_attempt",
        "games_attem_task_id_85c642_idx",
        "CREATE INDEX games_attem_task_id_85c642_idx ON games_attempt "
        "(task_id, anon_key, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_attempt",
        "games_attem_task_id_bd72cc_idx",
        "CREATE INDEX games_attem_task_id_bd72cc_idx ON games_attempt "
        "(task_id, status) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_hintattempt",
        "games_hinta_hint_id_7a53ab_idx",
        "CREATE INDEX games_hinta_hint_id_7a53ab_idx ON games_hintattempt "
        "(hint_id, team_id, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_hintattempt",
        "games_hinta_hint_id_dfdf44_idx",
        "CREATE INDEX games_hinta_hint_id_dfdf44_idx ON games_hintattempt "
        "(hint_id, user_id, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_hintattempt",
        "games_hinta_hint_id_c56ad5_idx",
        "CREATE INDEX games_hinta_hint_id_c56ad5_idx ON games_hintattempt "
        "(hint_id, anon_key, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
    (
        "games_hintattempt",
        "games_hinta_hint_id_a6f474_idx",
        "CREATE INDEX games_hinta_hint_id_a6f474_idx ON games_hintattempt "
        "(hint_id, is_real_request) ALGORITHM=INPLACE LOCK=NONE",
    ),
]


def _index_exists(cursor, schema: str, table: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = %s AND index_name = %s
        LIMIT 1
        """,
        [schema, table, index_name],
    )
    return cursor.fetchone() is not None


class Command(BaseCommand):
    help = "Ensure MySQL indexes from games.0109 exist (idempotent CREATE INDEX)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only print which indexes are missing; do not run DDL.",
        )
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Exit with code 1 if any index is missing (no DDL).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        check_only = options["check_only"]

        if connection.vendor != "mysql":
            raise CommandError(
                "This command only supports MySQL (RDS). "
                "Set RDS_HOSTNAME and related env vars so default uses django.db.backends.mysql."
            )

        schema = connection.settings_dict.get("NAME") or ""
        if not schema:
            raise CommandError("Database NAME is empty.")

        missing = []
        with connection.cursor() as cursor:
            for table, index_name, _ddl in INDEX_DDL:
                if not _index_exists(cursor, schema, table, index_name):
                    missing.append((table, index_name, _ddl))

        present = len(INDEX_DDL) - len(missing)
        self.stdout.write(
            f"Indexes present {present}/{len(INDEX_DDL)}; missing {len(missing)}."
        )
        for table, index_name, ddl in missing:
            self.stdout.write(f"  MISSING {table}.{index_name}")

        if check_only:
            if missing:
                raise CommandError(f"{len(missing)} index(es) missing.")
            self.stdout.write(self.style.SUCCESS("All indexes exist."))
            return

        if dry_run:
            for _t, _n, ddl in missing:
                self.stdout.write(f"  would run: {ddl}")
            return

        if not missing:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        self.stdout.write(self.style.WARNING(f"Creating {len(missing)} index(es); this may take a long time on large tables."))
        with connection.cursor() as cursor:
            for table, index_name, ddl in missing:
                self.stdout.write(f"Creating {index_name} on {table} ...")
                try:
                    cursor.execute(ddl)
                except Exception as exc:
                    err = str(exc)
                    if "Duplicate key name" in err or "1061" in err:
                        self.stdout.write(self.style.WARNING(f"  skip (already exists): {index_name}"))
                        continue
                    raise CommandError(f"Failed on {index_name}: {exc}") from exc
                self.stdout.write(self.style.SUCCESS(f"  done {index_name}"))

        self.stdout.write(self.style.SUCCESS("Finished creating indexes."))
        self.stdout.write(
            "If django_migrations does not list games 0109 yet, run: "
            "python manage.py migrate games 0109 --fake"
        )
