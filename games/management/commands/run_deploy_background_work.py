"""Run deferred production index work and the legacy ChainTaskState backfill.

This command is called once by the Green deploy orchestrator after EB reports
the new application version healthy. The MySQL named lock serializes concurrent
deploy invocations across all Green instances.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from games.management.commands._background_index_executor import (
    assert_schema_work_lock,
    ensure_indexes,
    schema_work_lock,
)
from games.management.commands.ensure_games_0109_indexes import INDEX_DDL as INDEX_DDL_0109
from games.management.commands.ensure_registration_indexes import INDEX_DDL as INDEX_DDL_0112


INDEX_DDL_0194 = [
    (
        "games_attempt",
        "games_attempt_skip_time_idx",
        "CREATE INDEX games_attempt_skip_time_idx ON games_attempt "
        "(skip, time) ALGORITHM=INPLACE LOCK=NONE",
    ),
]


class Command(BaseCommand):
    help = "Verify deferred indexes; optionally run the explicit ChainTaskState backfill."

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-chain-backfill",
            action="store_true",
            help="Explicitly replay all historical chain contexts under the global lock.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "mysql":
            raise CommandError("This command is only supported on the production MySQL database.")
        schema = connection.settings_dict.get("NAME") or ""
        if not schema:
            raise CommandError("Database NAME is empty.")

        indexes = INDEX_DDL_0109 + INDEX_DDL_0194 + INDEX_DDL_0112
        with schema_work_lock(self) as native_connection:
            ensure_indexes(self, indexes, schema, native_connection)

            assert_schema_work_lock(native_connection)
            if options["include_chain_backfill"]:
                self.stdout.write("Running explicitly requested ChainTaskState backfill.")
                call_command(
                    "backfill_chain_task_states",
                    require_schema_work_lock=True,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            else:
                self.stdout.write(
                    "ChainTaskState backfill not requested; it is not inferred from row counts."
                )
            assert_schema_work_lock(native_connection)

        self.stdout.write(self.style.SUCCESS("Controlled background work completed."))
