"""
Create the composite index on games_registration(game_id, team_id) using
online DDL (ALGORITHM=INPLACE LOCK=NONE) so it never blocks live traffic.

Same pattern as ensure_games_0109_indexes.  Safe to run multiple times
(idempotent: skips indexes that already exist).

Examples:
  python manage.py ensure_registration_indexes --dry-run
  python manage.py ensure_registration_indexes
  nohup python manage.py ensure_registration_indexes > /tmp/ensure_reg_idx.log 2>&1 &
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


INDEX_DDL = [
    (
        "games_registration",
        "games_reg_game_team_idx",
        "CREATE INDEX games_reg_game_team_idx ON games_registration "
        "(game_id, team_id) ALGORITHM=INPLACE LOCK=NONE",
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
    help = "Ensure composite index on games_registration(game_id, team_id) exists (idempotent)."

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
                "Set RDS_HOSTNAME and related env vars so the default DB uses django.db.backends.mysql."
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
        self.stdout.write(f"Indexes present {present}/{len(INDEX_DDL)}; missing {len(missing)}.")
        for table, index_name, _ in missing:
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

        self.stdout.write(
            self.style.WARNING(f"Creating {len(missing)} index(es); may take a while on large tables.")
        )
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
