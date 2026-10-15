"""Unit coverage for the controlled deployment database executor."""

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from games.management.commands import _background_index_executor as executor
from games.management.commands import run_deploy_background_work as deploy_work


class SchemaWorkLockTests(SimpleTestCase):
    def _connection_cursor(self, results):
        cursor = Mock()
        cursor.fetchone.side_effect = results

        @contextmanager
        def cursor_context():
            yield cursor

        return cursor, cursor_context

    def test_lock_is_released_on_normal_and_exceptional_exit(self):
        native = object()
        cursor, cursor_context = self._connection_cursor([(1,), (1,)])
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())
        fake_connection = SimpleNamespace(
            vendor="mysql", connection=native, ensure_connection=Mock(),
            cursor=Mock(side_effect=cursor_context),
        )
        with patch.object(executor, "connection", fake_connection):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                with executor.schema_work_lock(command):
                    raise RuntimeError("simulated failure")
        self.assertEqual(cursor.execute.call_args_list[-1].args[0],
                         "SELECT RELEASE_LOCK(%s)")
        self.assertFalse(command.stderr.getvalue())

    def test_lock_timeout_fails_without_release(self):
        native = object()
        cursor, cursor_context = self._connection_cursor([(0,)])
        command = SimpleNamespace(stdout=StringIO(), stderr=StringIO())
        fake_connection = SimpleNamespace(
            vendor="mysql", connection=native, ensure_connection=Mock(),
            cursor=Mock(side_effect=cursor_context),
        )
        with patch.object(executor, "connection", fake_connection):
            with self.assertRaisesRegex(CommandError, "within 30 seconds"):
                with executor.schema_work_lock(command):
                    self.fail("lock timeout must not enter the critical section")
        self.assertEqual(cursor.execute.call_count, 1)


class DeployWorkCommandTests(SimpleTestCase):
    def test_backfill_is_not_implicit_in_normal_deploy_work(self):
        @contextmanager
        def fake_lock(_command):
            yield object()

        command = deploy_work.Command()
        command.stdout = StringIO()
        command.stderr = StringIO()
        fake_connection = SimpleNamespace(vendor="mysql", settings_dict={"NAME": "test"})
        with patch.object(deploy_work, "connection", fake_connection), \
                patch.object(deploy_work, "schema_work_lock", fake_lock), \
                patch.object(deploy_work, "ensure_indexes"), \
                patch.object(deploy_work, "assert_schema_work_lock"), \
                patch.object(deploy_work, "call_command") as call_command:
            command.handle(include_chain_backfill=False)
        call_command.assert_not_called()
        self.assertIn("not requested", command.stdout.getvalue())
