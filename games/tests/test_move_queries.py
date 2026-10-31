"""A ladder move costs the same number of queries however long its history is."""
import uuid

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from games.ops.analytics.identity import attach_anon_cookie
from games.tests.test_client_event_receipts import _ClientEventFixture


class LadderMoveQueriesTests(_ClientEventFixture):
    def _correct_word_after(self, wrong_words, anon_key):
        client = Client()
        attach_anon_cookie(client, anon_key)
        for n in range(wrong_words):
            self._post_word(1, 'ZZ' + 'ABCDEFGH'[n], uuid.uuid4(), client=client, anon_key=anon_key)
        with CaptureQueriesContext(connection) as queries:
            response = self._post_word(1, 'BBB', uuid.uuid4(), client=client, anon_key=anon_key)
        # A correct word answers with the redrawn card, which lists every attempt.
        self.assertTrue(response['raddle_correct'])
        self.assertIn('update_task_html_new', response)
        return len(queries.captured_queries)

    def test_card_does_not_load_the_task_per_attempt(self):
        short = self._correct_word_after(1, 'move-queries-short')
        long = self._correct_word_after(6, 'move-queries-long')
        self.assertEqual(short, long)
