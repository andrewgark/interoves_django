import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone

from games.background.messages import parse_background_message


def _body(**overrides):
    payload = {
        'version': 1,
        'type': 'difficulty.refresh',
        'run_id': 'run-1',
        'scheduled_for': '2026-09-26T12:35:00+03:00',
        'dedupe_key': 'difficulty.refresh:2026-09-26T12:35',
        'payload': {},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


@contextmanager
def _lock(acquired):
    @contextmanager
    def _inner(name, *, ttl_seconds):
        _inner.calls.append((name, ttl_seconds))
        yield acquired

    _inner.calls = []
    yield _inner


@override_settings(ROOT_URLCONF='interoves_django.urls')
class BackgroundWorkerTests(SimpleTestCase):
    def _post(self, body, *, role='background-worker', sqsd=True):
        headers = {}
        if sqsd:
            headers['HTTP_USER_AGENT'] = 'aws-sqsd/2.0'
            headers['HTTP_X_AWS_SQSD_MSGID'] = 'message-1'
        with patch.dict('os.environ', {'INTEROVES_RUNTIME_ROLE': role}, clear=False):
            return Client().post(
                '/internal/worker/background/',
                body,
                content_type='application/json',
                **headers,
            )

    def test_web_role_is_disabled(self):
        with patch('games.views.background_worker.run_daily_difficulty_refresh') as refresh:
            response = self._post(_body(), role='web')
        self.assertEqual(response.status_code, 503)
        refresh.assert_not_called()

    def test_word_salad_worker_role_is_disabled(self):
        with patch('games.views.background_worker.run_daily_difficulty_refresh') as refresh:
            response = self._post(_body(), role='worker')
        self.assertEqual(response.status_code, 503)
        refresh.assert_not_called()

    def test_missing_sqsd_headers_are_rejected(self):
        response = self._post(_body(), sqsd=False)
        self.assertEqual(response.status_code, 403)

    def test_unsupported_type_is_rejected(self):
        response = self._post(_body(type='telegram.announcements'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'unsupported type')

    def test_stale_message_is_acked_without_refresh(self):
        scheduled = timezone.now() - timedelta(minutes=3)
        body = _body(scheduled_for=scheduled.isoformat())
        with patch('games.views.background_worker.run_daily_difficulty_refresh') as refresh:
            response = self._post(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_stale')
        refresh.assert_not_called()

    def test_held_lock_is_acked_without_refresh(self):
        scheduled = timezone.now().isoformat()
        with _lock(False) as lock, patch(
            'games.views.background_worker.distributed_cron_lock',
            lock,
        ), patch('games.views.background_worker.run_daily_difficulty_refresh') as refresh:
            response = self._post(_body(scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')
        self.assertEqual(lock.calls, [('daily_difficulty_refresh', 600)])
        refresh.assert_not_called()

    def test_sqsd_delivery_runs_one_refresh(self):
        scheduled = timezone.now().isoformat()
        with _lock(True) as lock, patch(
            'games.views.background_worker.distributed_cron_lock',
            lock,
        ), patch(
            'games.views.background_worker.run_daily_difficulty_refresh',
            return_value=[{'game_id': 'ladder'}],
        ) as refresh:
            response = self._post(_body(scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'refreshed': 1})
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.kwargs['worker'], 'background:message-1')
        self.assertEqual(refresh.call_args.kwargs['limit'], 10)

    def test_refresh_exception_is_retried(self):
        scheduled = timezone.now().isoformat()
        with _lock(True) as lock, patch(
            'games.views.background_worker.distributed_cron_lock',
            lock,
        ), patch(
            'games.views.background_worker.run_daily_difficulty_refresh',
            side_effect=RuntimeError('db down'),
        ):
            response = self._post(_body(scheduled_for=scheduled))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['status'], 'failed')

    def test_parser_requires_aware_schedule(self):
        with self.assertRaises(Exception):
            parse_background_message(_body(scheduled_for='2026-09-26T12:35:00'))

    def test_health_check_runs_existing_checker(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.run_daily_difficulty_health_check',
            return_value={'status': 'healthy'},
        ) as check, patch('games.views.background_worker.run_daily_difficulty_refresh') as refresh:
            response = self._post(_body(
                type='difficulty.health_check',
                scheduled_for=scheduled,
                dedupe_key='difficulty.health_check:hour',
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'healthy': True})
        check.assert_called_once_with()
        refresh.assert_not_called()

    def test_health_check_lock_skip_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.run_daily_difficulty_health_check',
            return_value={'status': 'skipped_locked'},
        ):
            response = self._post(_body(type='difficulty.health_check', scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_health_check_recent_skip_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.run_daily_difficulty_health_check',
            return_value={'status': 'skipped_recent'},
        ):
            response = self._post(_body(type='difficulty.health_check', scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_recent')

    def test_unhealthy_health_check_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.run_daily_difficulty_health_check',
            return_value={'status': 'unhealthy', 'detail': 'due'},
        ):
            response = self._post(_body(type='difficulty.health_check', scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'healthy': False})

    def test_projection_reconcile_is_dry_run(self):
        scheduled = timezone.now().isoformat()
        summary = {
            'skipped_locked': False,
            'mode': 'apply',
            'scanned': 3,
            'valid': 2,
            'missing': 1,
            'stale': 0,
            'rebuilt': 1,
            'invalid': [{'game_id': 'ladder', 'release': '1', 'task_group_id': 9, 'status': 'missing_state'}],
        }
        with patch(
            'games.views.background_worker.reconcile_projection_releases',
            return_value=summary,
        ) as reconcile, patch(
            'games.views.background_worker.start_queue_heartbeat',
            return_value=0.0,
        ), patch('games.views.background_worker.finish_queue_heartbeat'):
            response = self._post(_body(
                type='projection.reconcile',
                scheduled_for=scheduled,
                dedupe_key='projection.reconcile:minute',
            ))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['mode'], 'apply')
        self.assertEqual(body['missing'], 1)
        self.assertEqual(body['rebuilt'], 1)
        reconcile.assert_called_once_with(apply=True, limit=5)

    def test_projection_lock_skip_is_acked(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.reconcile_projection_releases',
            return_value={'skipped_locked': True, 'lines': []},
        ), patch(
            'games.views.background_worker.start_queue_heartbeat',
            return_value=0.0,
        ), patch('games.views.background_worker.finish_queue_heartbeat'):
            response = self._post(_body(type='projection.reconcile', scheduled_for=scheduled))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'skipped_locked')

    def test_health_check_exception_is_retried(self):
        scheduled = timezone.now().isoformat()
        with patch(
            'games.views.background_worker.run_daily_difficulty_health_check',
            side_effect=RuntimeError('db down'),
        ):
            response = self._post(_body(type='difficulty.health_check', scheduled_for=scheduled))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['status'], 'failed')

    def test_projection_refresh_ignores_stale_window(self):
        scheduled = timezone.now() - timedelta(minutes=4)
        with patch(
            'games.views.background_worker.run_named_projection_refresh',
            return_value='missing',
        ) as refresh:
            response = self._post(_body(
                type='projection.refresh',
                scheduled_for=scheduled.isoformat(),
                payload={'game_id': 'missing-release', 'task_group_id': 1, 'mode': 'actor'},
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'missing')
        refresh.assert_called_once_with(
            game_id='missing-release', task_group_id=1, mode='actor',
        )

    def test_projection_refresh_rejects_unknown_mode(self):
        scheduled = timezone.now().isoformat()
        response = self._post(_body(
            type='projection.refresh',
            scheduled_for=scheduled,
            payload={'game_id': 'ladder', 'task_group_id': 1, 'mode': 'user'},
        ))
        self.assertEqual(response.status_code, 400)
