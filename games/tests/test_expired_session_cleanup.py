from datetime import timedelta

from django.contrib.sessions.models import Session
from django.test import TestCase
from django.utils import timezone

from games.ops.cron.sessions import delete_expired_sessions


class DeleteExpiredSessionsTests(TestCase):
    def _make(self, prefix, count, *, delta):
        Session.objects.bulk_create(
            Session(
                session_key='{}{:036d}'.format(prefix, index),
                session_data='',
                expire_date=timezone.now() + delta,
            )
            for index in range(count)
        )

    def test_deletes_only_expired_rows_in_batches(self):
        self._make('old-', 7, delta=-timedelta(days=1))
        self._make('new-', 3, delta=timedelta(days=1))

        result = delete_expired_sessions(batch_size=3, max_batches=10)

        self.assertEqual(result, {'deleted': 7, 'complete': True})
        self.assertEqual(Session.objects.filter(session_key__startswith='old-').count(), 0)
        self.assertEqual(Session.objects.filter(session_key__startswith='new-').count(), 3)

    def test_batch_budget_leaves_rest_for_next_tick(self):
        self._make('old-', 7, delta=-timedelta(days=1))

        result = delete_expired_sessions(batch_size=3, max_batches=2)

        self.assertEqual(result, {'deleted': 6, 'complete': False})
        self.assertEqual(Session.objects.filter(session_key__startswith='old-').count(), 1)

    def test_empty_table_is_complete(self):
        self.assertEqual(delete_expired_sessions(), {'deleted': 0, 'complete': True})
