from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from games.puzzles.censorly.article import redact


class TitleOpeningMemoTests(SimpleTestCase):
    def setUp(self):
        redact._title_opening_memo.clear()
        cache.clear()
        self.addCleanup(redact._title_opening_memo.clear)
        self.addCleanup(cache.clear)

    def _payload(self, title):
        return {
            'title_tokens': [{'kind': 'content', 'surface': title, 'lemma': title.lower()}],
            'body_tokens': [{'kind': 'content', 'surface': 'кот', 'lemma': 'кот'}],
        }

    def test_each_article_is_matched_once_beyond_sixteen_articles(self):
        titles = ['Кот{}'.format(i) for i in range(20)]
        with patch.object(
            redact, '_title_opening_lemmas_cached', wraps=redact._title_opening_lemmas_cached,
        ) as compute:
            for _round in range(2):
                for title in titles:
                    redact.title_opening_lemmas(self._payload(title))
        self.assertEqual(compute.call_count, len(titles))

    def test_a_fresh_process_reuses_the_shared_cache(self):
        payload = self._payload('Кот')
        first = redact.title_opening_lemmas(payload)
        redact._title_opening_memo.clear()  # as in another process
        with patch.object(redact, '_title_opening_lemmas_cached') as compute:
            self.assertEqual(redact.title_opening_lemmas(payload), first)
        compute.assert_not_called()
