from django.test import SimpleTestCase

from games.site.http.tags.filters import vote_count


class VoteCountFilterTests(SimpleTestCase):
    # Same table as static/js/vote_stack.test.js (shortCount).
    CASES = [
        (0, '0'), (999, '999'), (1000, '1K'), (1099, '1K'), (1234, '1,2K'),
        (1999, '1,9K'), (9999, '9,9K'), (10000, '10K'), (12345, '12K'),
        (999999, '999K'), (1000000, '1M'), (1250000, '1,2M'), (12500000, '12M'),
    ]

    def test_short_labels_round_down(self):
        for n, label in self.CASES:
            with self.subTest(n=n):
                self.assertEqual(vote_count(n), label)

    def test_empty_is_zero(self):
        self.assertEqual(vote_count(None), '0')
