import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone

from games.telegram.shadow import telegram_shadow, telegram_shadow_active


def _body(**overrides):
    payload = {
        'version': 1,
        'type': 'telegram.announcements',
        'run_id': 'run-1',
        'scheduled_for': timezone.now().isoformat(),
        'dedupe_key': 'telegram.announcements:minute',
        'payload': {},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


@override_settings(ROOT_URLCONF='interoves_django.urls')
class IntegrationsWorkerTests(SimpleTestCase):
    def _post(self, body, *, role='integration-worker', sqsd=True):
        headers = {}
        if sqsd:
            headers['HTTP_USER_AGENT'] = 'aws-sqsd/2.0'
            headers['HTTP_X_AWS_SQSD_MSGID'] = 'message-1'
        with patch.dict('os.environ', {'INTEROVES_RUNTIME_ROLE': role}, clear=False):
            return Client().post(
                '/internal/worker/integrations/',
                body,
                content_type='application/json',
                **headers,
            )

    def test_web_role_is_disabled(self):
        with patch('games.views.integrations_worker.run_announcement_live') as shadow:
            response = self._post(_body(), role='web')
        self.assertEqual(response.status_code, 503)
        shadow.assert_not_called()

    def test_background_role_is_disabled(self):
        response = self._post(_body(), role='background-worker')
        self.assertEqual(response.status_code, 503)

    def test_missing_sqsd_headers_are_rejected(self):
        response = self._post(_body(), sqsd=False)
        self.assertEqual(response.status_code, 403)

    def test_difficulty_type_is_rejected(self):
        response = self._post(_body(type='difficulty.refresh'))
        self.assertEqual(response.status_code, 400)

    def test_stale_announcement_is_acked(self):
        scheduled = timezone.now() - timedelta(minutes=4)
        with patch('games.views.integrations_worker.run_announcement_live') as shadow:
            response = self._post(_body(scheduled_for=scheduled.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_stale')
        shadow.assert_not_called()

    def test_announcement_shadow_uses_scheduled_for(self):
        scheduled = timezone.now() - timedelta(seconds=30)
        with patch(
            'games.views.integrations_worker.run_announcement_live',
            return_value={'start': 1},
        ) as shadow:
            response = self._post(_body(scheduled_for=scheduled.isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['result']['start'], 1)
        shadow.assert_called_once()
        self.assertEqual(shadow.call_args.kwargs['now'], scheduled)

    def test_admin_report_read_after_the_window_still_runs(self):
        scheduled = timezone.make_aware(datetime(2026, 9, 26, 0, 27))
        with patch(
            'games.views.integrations_worker.run_admin_report_live',
            return_value={'sent': 1, 'skipped': 0},
        ) as shadow, patch(
            'games.views.integrations_worker.timezone.now',
            return_value=scheduled + timedelta(minutes=4),
        ):
            response = self._post(_body(
                type='telegram.admin_report',
                scheduled_for=scheduled.isoformat(),
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['result']['sent'], 1)
        shadow.assert_called_once()
        self.assertEqual(shadow.call_args.kwargs['now'], scheduled)

    def test_old_admin_report_is_stale(self):
        scheduled = timezone.now() - timedelta(minutes=21)
        with patch('games.views.integrations_worker.run_admin_report_live') as shadow:
            response = self._post(_body(
                type='telegram.admin_report',
                scheduled_for=scheduled.isoformat(),
            ))
        self.assertEqual(response.json()['status'], 'skipped_stale')
        shadow.assert_not_called()

    def test_held_admin_report_lock_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.integrations_worker.run_admin_report_live',
            return_value=None,
        ):
            response = self._post(_body(
                type='telegram.admin_report',
                scheduled_for=scheduled,
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_held_announcement_lock_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.integrations_worker.run_announcement_live',
            return_value=None,
        ):
            response = self._post(_body(scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_instagram_refresh_skips_a_young_token(self):
        with patch(
            'games.views.integrations_worker.run_instagram_token_refresh',
            return_value={'action': 'skipped', 'age_days': 4, 'max_age_days': 30},
        ) as refresh:
            response = self._post(_body(
                type='instagram.token_refresh',
                dedupe_key='instagram.token_refresh:day',
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['result']['action'], 'skipped')
        self.assertNotIn('access_token', response.json()['result'])
        refresh.assert_called_once()

    def test_instagram_lock_is_acked(self):
        with patch(
            'games.views.integrations_worker.run_instagram_token_refresh',
            return_value=None,
        ):
            response = self._post(_body(type='instagram.token_refresh'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_old_instagram_message_is_stale(self):
        scheduled = timezone.now() - timedelta(hours=13)
        with patch('games.views.integrations_worker.run_instagram_token_refresh') as refresh:
            response = self._post(_body(
                type='instagram.token_refresh',
                scheduled_for=scheduled.isoformat(),
            ))
        self.assertEqual(response.json()['status'], 'skipped_stale')
        refresh.assert_not_called()

    def test_social_publish_uses_scheduled_for(self):
        scheduled = timezone.now() - timedelta(seconds=20)
        with patch(
            'games.views.integrations_worker.run_social_publish_live',
            return_value={'telegram': 0, 'twitter': 0, 'instagram': 0, 'threads': 0, 'errors': 0},
        ) as publish:
            response = self._post(_body(
                type='social.publish',
                dedupe_key='social.publish:minute',
                scheduled_for=scheduled.isoformat(),
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs['now'], scheduled)

    def test_social_publish_lock_is_acked(self):
        with patch(
            'games.views.integrations_worker.run_social_publish_live',
            return_value=None,
        ):
            response = self._post(_body(type='social.publish'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_old_social_publish_is_stale(self):
        scheduled = timezone.now() - timedelta(minutes=4)
        with patch('games.views.integrations_worker.run_social_publish_live') as publish:
            response = self._post(_body(
                type='social.publish',
                scheduled_for=scheduled.isoformat(),
            ))
        self.assertEqual(response.json()['status'], 'skipped_stale')
        publish.assert_not_called()

    def test_shadow_flag_does_not_leak(self):
        self.assertFalse(telegram_shadow_active())
        with telegram_shadow():
            self.assertTrue(telegram_shadow_active())
        self.assertFalse(telegram_shadow_active())


class InstagramRefreshTests(SimpleTestCase):
    def test_young_token_does_not_call_the_api(self):
        from types import SimpleNamespace

        from games.instagram.refresh import run_instagram_token_refresh

        row = SimpleNamespace(refreshed_at=timezone.now() - timedelta(days=4))
        with patch(
            'games.instagram.refresh.distributed_cron_lock',
        ) as lock, patch(
            'games.instagram.refresh.InstagramToken.get',
            return_value=row,
        ), patch(
            'games.instagram.refresh.refresh_and_persist',
        ) as api, patch(
            'games.instagram.refresh.start_queue_heartbeat',
            return_value=1,
        ), patch(
            'games.instagram.refresh.finish_queue_heartbeat',
        ):
            lock.return_value.__enter__.return_value = True
            result = run_instagram_token_refresh()
        self.assertEqual(result['action'], 'skipped')
        self.assertEqual(result['age_days'], 4)
        api.assert_not_called()
        self.assertNotIn('access_token', result)
