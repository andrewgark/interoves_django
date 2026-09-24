from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from games.models import QueueWorkerHeartbeat
from games.queue_heartbeat import finish_record, start
from games.support.queues import dashboard_context


class QueueHeartbeatTests(TestCase):
    def test_success_is_preserved_when_a_later_cron_run_fails(self):
        start('test_queue', worker='cron:test')
        finish_record('test_queue', success=True, processed_count=3)
        successful_at = QueueWorkerHeartbeat.objects.get(pk='test_queue').last_success_at

        start('test_queue', worker='cron:test')
        finish_record('test_queue', success=False, error='boom')
        row = QueueWorkerHeartbeat.objects.get(pk='test_queue')

        self.assertEqual(row.status, QueueWorkerHeartbeat.STATUS_FAILED)
        self.assertEqual(row.last_success_at, successful_at)
        self.assertEqual(row.last_error, 'boom')

    def test_dashboard_marks_a_stuck_running_worker_stale(self):
        row = QueueWorkerHeartbeat.objects.create(
            queue_name='word_salad_recheck',
            status=QueueWorkerHeartbeat.STATUS_RUNNING,
            started_at=timezone.now() - timedelta(minutes=11),
        )
        QueueWorkerHeartbeat.objects.filter(pk=row.pk).update(
            updated_at=timezone.now() - timedelta(minutes=11),
        )

        heartbeat = next(
            item for item in dashboard_context()['heartbeat_rows']
            if item['queue_name'] == 'word_salad_recheck'
        )
        self.assertEqual(heartbeat['health'], 'stale')
