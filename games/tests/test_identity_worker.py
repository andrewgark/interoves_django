import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase


def _body(**overrides):
    payload = {
        'version': 1,
        'type': 'anonymous.merge',
        'run_id': 'run-1',
        'scheduled_for': '2026-09-26T12:35:00+03:00',
        'dedupe_key': 'anonymous.merge:job',
        'payload': {'job_id': '11111111-1111-1111-1111-111111111111'},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


class IdentityWorkerTests(SimpleTestCase):
    def _post(self, body, *, role='identity-worker', sqsd=True):
        headers = {}
        if sqsd:
            headers['HTTP_USER_AGENT'] = 'aws-sqsd/2.0'
            headers['HTTP_X_AWS_SQSD_MSGID'] = 'message-1'
        with patch.dict('os.environ', {'INTEROVES_RUNTIME_ROLE': role}, clear=False):
            return Client().post(
                '/internal/worker/identity/',
                body,
                content_type='application/json',
                **headers,
            )

    def test_web_role_is_disabled(self):
        with patch('games.views.identity_worker.run_named_merge_job') as run:
            response = self._post(_body(), role='web')
        self.assertEqual(response.status_code, 503)
        run.assert_not_called()

    def test_missing_sqsd_headers_are_rejected(self):
        response = self._post(_body(), sqsd=False)
        self.assertEqual(response.status_code, 403)

    def test_queue_scan_message_is_rejected(self):
        response = self._post(_body(payload={}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'job_id is required')

    def test_other_type_is_rejected(self):
        response = self._post(_body(type='difficulty.refresh', payload={}))
        self.assertEqual(response.status_code, 400)

    def test_named_job_is_acked(self):
        with patch(
            'games.views.identity_worker.run_named_merge_job',
            return_value={'status': 'missing'},
        ) as run:
            response = self._post(_body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'missing')
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['worker'], 'identity:message-1')

    def test_reconcile_publishes_without_claiming(self):
        with patch(
            'games.views.identity_worker.publish_unmarked_due_merge_jobs',
            return_value=2,
        ) as publish, patch(
            'games.views.identity_worker.run_named_merge_job',
        ) as run, patch(
            'games.views.identity_worker.start_queue_heartbeat',
            return_value=1,
        ), patch(
            'games.views.identity_worker.finish_queue_heartbeat',
        ) as finish:
            response = self._post(_body(
                type='anonymous.merge_reconcile',
                dedupe_key='anonymous.merge_reconcile',
                payload={},
            ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'published': 2})
        publish.assert_called_once_with(limit=5)
        run.assert_not_called()
        finish.assert_called_once()
        self.assertTrue(finish.call_args.kwargs['success'])
