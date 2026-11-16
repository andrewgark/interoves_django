from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from games.ops.social.threads.api import current_access_token
from games.ops.social.threads.models import ThreadsToken
from games.ops.social.threads.refresh import run_threads_token_refresh

REFRESH = 'games.ops.social.threads.refresh.refresh_access_token'


def _age(days):
    ThreadsToken.objects.filter(pk=1).update(
        refreshed_at=timezone.now() - timedelta(days=days),
    )


@override_settings(THREADS_ACCESS_TOKEN='seed-1')
class ThreadsTokenRefreshTests(TestCase):
    def test_first_run_seeds_db_from_settings(self):
        with patch(REFRESH) as refresh:
            result = run_threads_token_refresh()
        self.assertEqual(result, {'action': 'seeded'})
        refresh.assert_not_called()
        self.assertEqual(ThreadsToken.get().access_token, 'seed-1')

    def test_young_token_is_not_refreshed(self):
        ThreadsToken.objects.create(access_token='live', seed_token='seed-1')
        _age(5)
        with patch(REFRESH) as refresh:
            result = run_threads_token_refresh()
        self.assertEqual(result['action'], 'skipped')
        refresh.assert_not_called()

    def test_old_token_is_rotated_and_read_db_first(self):
        ThreadsToken.objects.create(access_token='live', seed_token='seed-1')
        _age(31)
        with patch(REFRESH, return_value={
            'access_token': 'rotated', 'expires_in': 60 * 86400,
        }) as refresh:
            result = run_threads_token_refresh()
        refresh.assert_called_once_with('live')
        self.assertEqual(result, {'action': 'refreshed', 'expires_days': 60})
        row = ThreadsToken.get()
        self.assertEqual(row.access_token, 'rotated')
        self.assertEqual(row.seed_token, 'seed-1')
        self.assertIsNotNone(row.expires_at)
        self.assertEqual(current_access_token(), 'rotated')

    def test_refresh_failure_keeps_token(self):
        ThreadsToken.objects.create(access_token='live', seed_token='seed-1')
        _age(31)
        with patch(REFRESH, side_effect=RuntimeError('expired')):
            result = run_threads_token_refresh()
        self.assertEqual(result, {'action': 'failed'})
        self.assertEqual(ThreadsToken.get().access_token, 'live')

    @override_settings(THREADS_ACCESS_TOKEN='seed-2')
    def test_reissued_settings_token_replaces_db_token(self):
        ThreadsToken.objects.create(access_token='expired', seed_token='seed-1')
        _age(40)
        with patch(REFRESH) as refresh:
            result = run_threads_token_refresh()
        self.assertEqual(result, {'action': 'reseeded'})
        refresh.assert_not_called()
        row = ThreadsToken.get()
        self.assertEqual((row.access_token, row.seed_token), ('seed-2', 'seed-2'))
        self.assertEqual(current_access_token(), 'seed-2')
