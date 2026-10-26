"""Classify and apply a release's pending migrations before Green switches code.

Green instances never run ``migrate`` themselves (RUN_PRODUCTION_MIGRATIONS is
off), so the deploy orchestrator runs this command from the release source
through an RDS tunnel before ``update-environment``. Old code keeps serving
during and after the migration, so only expanding operations are applied
automatically; anything that can break old code or hold long locks stops the
deploy until a human applies it deliberately.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from games.management.commands._background_index_executor import schema_work_lock

EXIT_UNSAFE = 3

# Operations that never run automatically: arbitrary code or SQL whose effect
# and duration cannot be judged from the operation itself.
_MANUAL_OPERATIONS = (
    migrations.RunPython,
    migrations.RunSQL,
    migrations.SeparateDatabaseAndState,
)
# Index/constraint work is cheap on a table created earlier in the same plan,
# but can lock or fail on duplicates for an existing production table.
_NEW_TABLE_ONLY_OPERATIONS = (
    migrations.AddIndex,
    migrations.AddConstraint,
    migrations.AlterUniqueTogether,
    migrations.AlterIndexTogether,
)


def _operation_sql(operation, app_label, from_state, to_state):
    with connection.schema_editor(collect_sql=True, atomic=False) as editor:
        operation.database_forwards(app_label, editor, from_state, to_state)
    return [sql for sql in editor.collected_sql if sql.strip()]


def classify_operation(operation, app_label, sql, created_models):
    """Return a reason string when the operation is unsafe, else None."""
    if isinstance(operation, _MANUAL_OPERATIONS):
        return 'arbitrary code/SQL'
    if not sql:
        return None
    if isinstance(operation, migrations.CreateModel):
        return None
    model_name = getattr(operation, 'model_name_lower', None) or getattr(operation, 'name_lower', '')
    model_key = (app_label, model_name)
    if model_key in created_models:
        return None
    if isinstance(operation, _NEW_TABLE_ONLY_OPERATIONS):
        return 'index/constraint on an existing table'
    if isinstance(operation, migrations.AddField):
        field = operation.field
        if field.many_to_many:
            return None
        if field.unique or field.primary_key:
            return 'unique column on an existing table'
        if field.null or field.has_default():
            return None
        return 'NOT NULL column without default'
    return 'changes existing schema'


def release_migration_report(executor, plan):
    """Yield (migration, operation, sql, unsafe_reason) for a forward plan."""
    created_models = set()
    for migration, backwards in plan:
        if backwards:
            raise CommandError(f'Refusing a backwards plan step: {migration}.')
        state = executor.loader.project_state(
            (migration.app_label, migration.name), at_end=False,
        )
        for operation in migration.operations:
            to_state = state.clone()
            operation.state_forwards(migration.app_label, to_state)
            sql = _operation_sql(operation, migration.app_label, state, to_state)
            reason = classify_operation(operation, migration.app_label, sql, created_models)
            if isinstance(operation, migrations.CreateModel):
                created_models.add((migration.app_label, operation.name_lower))
            yield migration, operation, sql, reason
            state = to_state


class Command(BaseCommand):
    help = "Show, classify and optionally apply the release's pending migrations."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Apply the plan when every operation is expanding.',
        )
        parser.add_argument(
            '--allow-unsafe', action='store_true',
            help='Apply even operations that may break the old code still serving.',
        )

    def handle(self, *args, **options):
        if options['apply'] and connection.vendor == 'mysql':
            with schema_work_lock(self):
                self._run(options)
        else:
            self._run(options)

    def _run(self, options):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            self.stdout.write('No pending migrations in this release.')
            return

        unsafe = []
        for migration, operation, sql, reason in release_migration_report(executor, plan):
            label = f'{migration.app_label}.{migration.name}: {operation.describe()}'
            if reason:
                unsafe.append(f'{label} ({reason})')
                self.stdout.write(f'  UNSAFE {label} ({reason})')
            else:
                self.stdout.write(f'  ok     {label}' + ('' if sql else ' [no SQL]'))

        if unsafe and not options['allow_unsafe']:
            self.stderr.write(
                'Release has migrations that may break the code still serving:\n  '
                + '\n  '.join(unsafe)
                + '\nApply them deliberately (separate release or manual migrate), '
                'or rerun with --allow-unsafe.'
            )
            raise SystemExit(EXIT_UNSAFE)
        if not options['apply']:
            self.stdout.write('Plan is safe to apply before switching code.')
            return
        call_command('migrate', interactive=False, stdout=self.stdout, stderr=self.stderr)
        self.stdout.write('Release migrations applied.')
