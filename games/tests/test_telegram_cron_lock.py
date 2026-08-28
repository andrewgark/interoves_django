from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class _FakeCursor:
    def __init__(self, queries, get_lock_result):
        self.queries = queries
        self.get_lock_result = get_lock_result
        self.last_sql = ''

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.last_sql = sql
        self.queries.append((sql, params))

    def fetchone(self):
        if 'GET_LOCK' in self.last_sql:
            return (self.get_lock_result,)
        return (1,)


class _FakeMySQLConnection:
    vendor = 'mysql'

    def __init__(self, get_lock_result):
        self.connection = object()
        self.get_lock_result = get_lock_result
        self.queries = []

    def ensure_connection(self):
        return None

    def cursor(self):
        return _FakeCursor(self.queries, self.get_lock_result)


class TelegramCronLockTests(SimpleTestCase):
    def test_mysql_lock_uses_same_connection_and_releases(self):
        from games.telegram import cron_lock

        fake_connection = _FakeMySQLConnection(get_lock_result=1)
        original_connection = fake_connection.connection

        with patch.object(cron_lock, 'connection', fake_connection):
            with cron_lock.telegram_cron_lock() as acquired:
                self.assertTrue(acquired)
                self.assertIs(fake_connection.connection, original_connection)

        self.assertEqual(len(fake_connection.queries), 2)
        self.assertIn('GET_LOCK', fake_connection.queries[0][0])
        self.assertIn('RELEASE_LOCK', fake_connection.queries[1][0])

    def test_mysql_lock_releases_when_cron_body_raises(self):
        from games.telegram import cron_lock

        fake_connection = _FakeMySQLConnection(get_lock_result=1)
        with self.assertRaises(RuntimeError):
            with patch.object(cron_lock, 'connection', fake_connection):
                with cron_lock.telegram_cron_lock() as acquired:
                    self.assertTrue(acquired)
                    raise RuntimeError('cron failed')

        self.assertEqual(len(fake_connection.queries), 2)
        self.assertIn('RELEASE_LOCK', fake_connection.queries[1][0])

    def test_mysql_held_lock_is_not_released_by_loser(self):
        from games.telegram import cron_lock

        fake_connection = _FakeMySQLConnection(get_lock_result=0)
        with patch.object(cron_lock, 'connection', fake_connection):
            with cron_lock.telegram_cron_lock() as acquired:
                self.assertFalse(acquired)

        self.assertEqual(len(fake_connection.queries), 1)
        self.assertIn('GET_LOCK', fake_connection.queries[0][0])

    def test_management_command_skips_when_global_lock_is_held(self):
        @contextmanager
        def held_lock():
            yield False

        stdout = StringIO()
        with (
            patch(
                'games.management.commands.telegram_game_announcements.telegram_cron_lock',
                held_lock,
            ),
            patch(
                'games.management.commands.telegram_game_announcements.process_game_announcements'
            ) as process_mock,
        ):
            call_command('telegram_game_announcements', stdout=stdout)

        process_mock.assert_not_called()
        self.assertIn('telegram cron skipped: lock held', stdout.getvalue())
