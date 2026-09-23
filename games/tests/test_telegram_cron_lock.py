from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class TelegramCronLockTests(SimpleTestCase):
    def test_wrapper_delegates_to_distributed_lease(self):
        calls = []

        @contextmanager
        def fake_lock(name, *, ttl_seconds):
            calls.append((name, ttl_seconds))
            yield True

        from games.telegram import cron_lock

        with patch.object(cron_lock, 'distributed_cron_lock', fake_lock):
            with cron_lock.telegram_cron_lock() as acquired:
                self.assertTrue(acquired)

        self.assertEqual(calls, [('telegram_game_announcements', 300)])

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
