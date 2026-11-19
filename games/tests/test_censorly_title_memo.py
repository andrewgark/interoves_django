from unittest.mock import patch

from django.test import SimpleTestCase

from games.puzzles.censorly.article import redact


class TitleOpeningMemoTests(SimpleTestCase):
    def setUp(self):
        redact._title_opening_memo.clear()
        self.addCleanup(redact._title_opening_memo.clear)

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
