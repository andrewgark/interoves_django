from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase

from games.management.commands.apply_release_migrations import _operation_sql, classify_operation

ROOT = Path(__file__).resolve().parents[2]
SQL = ['ALTER TABLE x']


class ClassifyReleaseMigrationTests(SimpleTestCase):
    def classify(self, operation, sql=SQL, created=()):
        return classify_operation(operation, 'games', sql, set(created))

    def test_expanding_operations_are_safe(self):
        self.assertIsNone(self.classify(migrations.CreateModel('Thing', fields=[])))
        self.assertIsNone(self.classify(
            migrations.AddField('task', 'note', models.TextField(null=True)),
        ))
        self.assertIsNone(self.classify(
            migrations.AddField('task', 'flag', models.BooleanField(default=False)),
        ))

    def test_state_only_alter_field_is_safe(self):
        operation = migrations.AlterField('task', 'name', models.CharField(max_length=10))
        self.assertIsNone(self.classify(operation, sql=[]))
        self.assertEqual(self.classify(operation), 'changes existing schema')

    def test_constraints_are_safe_only_on_tables_created_in_the_plan(self):
        operation = migrations.AddConstraint(
            'raddlecluelike',
            models.UniqueConstraint(fields=['task'], name='uniq_test'),
        )
        self.assertIsNone(self.classify(operation, created=[('games', 'raddlecluelike')]))
        self.assertEqual(self.classify(operation), 'index/constraint on an existing table')

    def test_destructive_and_code_operations_are_unsafe(self):
        self.assertEqual(self.classify(migrations.RemoveField('task', 'name')), 'changes existing schema')
        self.assertEqual(self.classify(migrations.DeleteModel('Task')), 'changes existing schema')
        self.assertEqual(self.classify(migrations.RenameField('task', 'a', 'b')), 'changes existing schema')
        self.assertEqual(self.classify(migrations.RunPython(migrations.RunPython.noop), sql=[]), 'arbitrary code/SQL')
        self.assertEqual(self.classify(migrations.RunSQL('SELECT 1'), sql=[]), 'arbitrary code/SQL')
        self.assertEqual(
            self.classify(migrations.AddField('task', 'n', models.IntegerField())),
            'NOT NULL column without default',
        )
        self.assertEqual(
            self.classify(migrations.AddField('task', 'u', models.CharField(max_length=5, null=True, unique=True))),
            'unique column on an existing table',
        )

    def test_planning_never_executes_run_python(self):
        def forwards(apps, schema_editor):
            raise AssertionError('RunPython must not run while planning')

        operation = migrations.RunPython(forwards)
        self.assertEqual(_operation_sql(operation, 'games', None, None), [])

    def test_green_deploy_applies_release_migrations_before_switching_code(self):
        script = (ROOT / 'scripts/deploy_green.sh').read_text()
        check = script.index('\nrelease_migrations\n')
        upload = script.index('aws s3 cp "$bundle"')
        apply = script.index('release_migrations --apply')
        update_environment = script.index('elasticbeanstalk update-environment')
        self.assertLess(check, upload)
        self.assertLess(apply, update_environment)
        self.assertIn('manage.py apply_release_migrations', script)


class ApplyReleaseMigrationsCommandTests(TestCase):
    def test_migrated_database_has_no_pending_plan(self):
        out = StringIO()
        call_command('apply_release_migrations', stdout=out)
        self.assertIn('No pending migrations in this release.', out.getvalue())
