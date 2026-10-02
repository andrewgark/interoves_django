from django.core.cache import cache
from django.test import SimpleTestCase

from games.support.queue_observatory.transport import (
    SNAPSHOT_CACHE_KEY,
    transport_snapshot,
)


class FakeSqs:
    def get_queue_attributes(self, *, QueueUrl, AttributeNames):
        name = QueueUrl.rsplit('/', 1)[-1]
        values = {
            'interoves-integrations-dlq': '27',
            'interoves-background-dlq': '1',
        }
        return {'Attributes': {
            'ApproximateNumberOfMessages': values.get(name, '0'),
            'ApproximateNumberOfMessagesNotVisible': '2' if name == 'interoves-recheck' else '0',
            'ApproximateNumberOfMessagesDelayed': '0',
        }}


class FakeEcs:
    def describe_services(self, *, cluster, services):
        service = services[0]
        return {'services': [{
            'desiredCount': 1,
            'runningCount': 1,
            'pendingCount': 0,
            'taskDefinition': 'arn:aws:ecs:eu-central-1:123:task-definition/{}:7'.format(service),
            'deployments': [{'status': 'PRIMARY', 'rolloutState': 'COMPLETED'}],
        }]}


class QueueTransportTests(SimpleTestCase):
    def setUp(self):
        cache.delete(SNAPSHOT_CACHE_KEY)

    def test_snapshot_separates_sqs_and_ecs_state_and_preserves_dlq_counts(self):
        payload = transport_snapshot(
            force=True,
            clients={'sqs': FakeSqs(), 'ecs': FakeEcs()},
        )

        self.assertEqual(payload['status'], 'ok')
        rows = {row['name']: row for row in payload['workers']}
        self.assertEqual(rows['integrations']['queue_state']['visible'], 0)
        self.assertEqual(rows['integrations']['dlq_state']['visible'], 27)
        self.assertEqual(rows['background']['dlq_state']['visible'], 1)
        self.assertEqual(rows['recheck']['queue_state']['in_flight'], 2)
        self.assertEqual(rows['identity']['service_state']['running'], 1)
        self.assertEqual(rows['identity']['service_state']['deployment'], 'COMPLETED')
        self.assertEqual(rows['integrations']['health'], 'error')

    def test_access_errors_are_unknown_not_zero(self):
        class DeniedSqs(FakeSqs):
            def get_queue_attributes(self, *, QueueUrl, AttributeNames):
                raise PermissionError('denied')

        payload = transport_snapshot(
            force=True,
            clients={'sqs': DeniedSqs(), 'ecs': FakeEcs()},
        )
        rows = {row['name']: row for row in payload['workers']}
        self.assertEqual(payload['status'], 'degraded')
        self.assertEqual(rows['recheck']['queue_state']['status'], 'unknown')
        self.assertIsNone(rows['recheck']['queue_state'].get('visible'))
        self.assertEqual(rows['recheck']['health'], 'unknown')

    def test_aws_error_code_is_preserved_for_diagnosis(self):
        class FakeAccessDenied(Exception):
            response = {'Error': {'Code': 'AccessDeniedException'}}

        class AwsDeniedSqs(FakeSqs):
            def get_queue_attributes(self, *, QueueUrl, AttributeNames):
                raise FakeAccessDenied('denied')

        payload = transport_snapshot(
            force=True,
            clients={'sqs': AwsDeniedSqs(), 'ecs': FakeEcs()},
        )
        rows = {row['name']: row for row in payload['workers']}
        self.assertEqual(rows['identity']['queue_state']['reason'], 'AccessDeniedException')
