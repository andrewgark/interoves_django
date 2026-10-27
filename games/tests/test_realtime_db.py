from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from games.realtime import db as realtime_db


class BoundedLockWaitTests(SimpleTestCase):
    def _mysql_connection(self):
        cursor = MagicMock()
        connection = MagicMock(vendor='mysql')
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.is_usable.return_value = True
        return connection, cursor

    def test_sets_and_restores_session_lock_wait_on_mysql(self):
        connection, cursor = self._mysql_connection()
        with patch.object(realtime_db, 'connection', connection):
            with realtime_db.bounded_lock_wait(2):
                cursor.execute('SELECT 1')
        statements = [call.args for call in cursor.execute.call_args_list]
        self.assertEqual(statements, [
            ('SET SESSION innodb_lock_wait_timeout = %s', [2]),
            ('SELECT 1',),
            ('SET SESSION innodb_lock_wait_timeout = DEFAULT',),
        ])

    def test_restores_lock_wait_when_block_raises(self):
        connection, cursor = self._mysql_connection()
        with patch.object(realtime_db, 'connection', connection):
            with self.assertRaises(RuntimeError):
                with realtime_db.bounded_lock_wait(2):
                    raise RuntimeError('lock wait timeout')
        self.assertEqual(
            cursor.execute.call_args_list[-1].args,
            ('SET SESSION innodb_lock_wait_timeout = DEFAULT',),
        )

    def test_is_a_no_op_on_other_databases(self):
        connection = MagicMock(vendor='sqlite')
        with patch.object(realtime_db, 'connection', connection):
            with realtime_db.bounded_lock_wait(2):
                pass
        connection.cursor.assert_not_called()
