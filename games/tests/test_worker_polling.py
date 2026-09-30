from unittest.mock import patch
import time

from django.test import SimpleTestCase

from games.worker_polling import poll_once, queue_url_for


class FakeSqs:
    def __init__(self, messages):
        self.messages = messages
        self.deleted = []
        self.visibility_changes = []

    def receive_message(self, **kwargs):
        self.receive_kwargs = kwargs
        return {'Messages': self.messages}

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    def change_message_visibility(self, **kwargs):
        self.visibility_changes.append(kwargs)


class WorkerPollingTests(SimpleTestCase):
    def test_queue_url_prefers_worker_specific_setting(self):
        spec_env = {'BACKGROUND_SQS_QUEUE_URL': 'specific', 'WORKER_QUEUE_URL': 'generic'}
        from games.worker_contract import WORKER_REGISTRY
        self.assertEqual(queue_url_for(WORKER_REGISTRY.get('background'), spec_env), 'specific')

    def test_success_is_deleted(self):
        sqs = FakeSqs([{'MessageId': 'm1', 'ReceiptHandle': 'r1', 'Body': '{}'}])
        with patch('games.worker_polling._deliver', return_value=200):
            result = poll_once(worker_name='background', client=sqs, queue_url='queue')
        self.assertEqual(result['status'], 'ack')
        self.assertEqual(len(sqs.deleted), 1)

    def test_transient_failure_is_left_for_retry(self):
        sqs = FakeSqs([{'MessageId': 'm1', 'ReceiptHandle': 'r1', 'Body': '{}'}])
        with patch('games.worker_polling._deliver', return_value=500):
            result = poll_once(worker_name='background', client=sqs, queue_url='queue')
        self.assertEqual(result['status'], 'retry')
        self.assertEqual(sqs.deleted, [])

    def test_invalid_payload_is_dropped(self):
        sqs = FakeSqs([{'MessageId': 'm1', 'ReceiptHandle': 'r1', 'Body': '{}'}])
        with patch('games.worker_polling._deliver', return_value=400):
            result = poll_once(worker_name='background', client=sqs, queue_url='queue')
        self.assertEqual(result['status'], 'drop')
        self.assertEqual(len(sqs.deleted), 1)

    def test_identity_extends_visibility_during_long_handler(self):
        sqs = FakeSqs([{'MessageId': 'm1', 'ReceiptHandle': 'r1', 'Body': '{}'}])

        def slow_handler(*args, **kwargs):
            time.sleep(0.03)
            return 200

        with patch('games.worker_polling.VISIBILITY_HEARTBEAT_INTERVAL', 0.01), \
             patch('games.worker_polling._deliver', side_effect=slow_handler):
            result = poll_once(
                worker_name='identity',
                client=sqs,
                queue_url='queue',
                visibility_timeout=30,
            )

        self.assertEqual(result['status'], 'ack')
        self.assertTrue(sqs.visibility_changes)
        self.assertEqual(sqs.visibility_changes[0]['QueueUrl'], 'queue')
        self.assertEqual(sqs.visibility_changes[0]['ReceiptHandle'], 'r1')
        self.assertEqual(sqs.visibility_changes[0]['VisibilityTimeout'], 30)

    def test_non_identity_worker_does_not_change_visibility(self):
        sqs = FakeSqs([{'MessageId': 'm1', 'ReceiptHandle': 'r1', 'Body': '{}'}])
        with patch('games.worker_polling._deliver', return_value=200):
            poll_once(
                worker_name='background',
                client=sqs,
                queue_url='queue',
                visibility_timeout=30,
            )
        self.assertEqual(sqs.visibility_changes, [])
