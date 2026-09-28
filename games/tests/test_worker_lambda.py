from unittest.mock import patch

from django.test import SimpleTestCase

from games.worker_lambda import handle_sqs_event


class WorkerLambdaTests(SimpleTestCase):
    def _event(self, *records):
        return {'Records': [
            {'messageId': message_id, 'body': '{}'}
            for message_id in records
        ]}

    def test_successful_records_are_acknowledged(self):
        with patch('games.worker_lambda._deliver', return_value=200):
            result = handle_sqs_event(self._event('one'), worker_name='background')
        self.assertEqual(result['batchItemFailures'], [])
        self.assertEqual(result['results'][0]['action'], 'ack')

    def test_transient_record_is_returned_for_partial_retry(self):
        with patch('games.worker_lambda._deliver', side_effect=[200, 500, 409]):
            result = handle_sqs_event(self._event('one', 'two', 'three'), worker_name='background')
        self.assertEqual(
            result['batchItemFailures'],
            [{'itemIdentifier': 'two'}, {'itemIdentifier': 'three'}],
        )

    def test_poison_record_is_not_retried(self):
        with patch('games.worker_lambda._deliver', return_value=400):
            result = handle_sqs_event(self._event('poison'), worker_name='background')
        self.assertEqual(result['batchItemFailures'], [])
        self.assertEqual(result['results'][0]['action'], 'drop')

    def test_unknown_worker_cannot_be_inferred_from_arn(self):
        with self.assertRaises(ValueError):
            handle_sqs_event({'Records': [], 'eventSourceARN': 'arn:queue:unknown'})
