"""Offline Censorly metrics: word groups, link groups and label scoring."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from games.puzzles.censorly.offline import metrics as m


class CensorlyMetricsTests(SimpleTestCase):
    def test_word_groups(self):
        self.assertEqual(m.word_group('пчела'), 'common')
        self.assertEqual(m.word_group('александр'), 'proper')
        self.assertEqual(m.word_group('м'), 'short')
        self.assertEqual(m.word_group('рубио'), 'unknown')

    def test_relation_is_the_live_one(self):
        self.assertTrue(m.opens('пчела', 'пчелиный'))
        self.assertFalse(m.opens('вода', 'водить'))
        # Same word is not an opening to judge.
        self.assertFalse(m.opens('пчела', 'пчела'))

    def test_link_groups(self):
        self.assertEqual(m.link_group('км', 'километр'), 'alias')
        self.assertEqual(m.link_group('пчела', 'пчелиный'), 'kuznetsova')
        self.assertEqual(m.link_group('венгрия', 'венгерский'), 'proper')

    def test_linked_means_another_word_opens_it(self):
        self.assertTrue(m.is_linked('пчелиный'))
        self.assertTrue(m.is_linked('пчела'))
        corpus = m.Corpus(source='t', articles=1, service_tokens=0)
        corpus.tokens.update({'пчелиный': 3})
        coverage = m.coverage(corpus)
        self.assertEqual(coverage.share('common', linked=True), 1.0)

    def test_linked_sees_roots_from_another_reading(self):
        # No bank row: the live pack takes примерно's roots from примерный.
        self.assertTrue(m.is_linked('примерно'))

    def test_deliberate_orphans_leave_the_queue_only(self):
        self.assertIn('образ', m.deliberate_orphans())
        corpus = m.Corpus(source='t', articles=1, service_tokens=0)
        corpus.tokens.update({'образ': 5})
        self.assertNotIn('образ', [lemma for _tokens, lemma, _kind in m.orphans(corpus, 10)])

    def test_wilson_interval(self):
        low, high = m.wilson(27, 30)
        self.assertLess(low, 0.9)
        self.assertGreater(high, 0.9)
        self.assertEqual(m.wilson(0, 0), (0.0, 1.0))

    def test_score_tracks_fixed_and_lost_pairs(self):
        sample = m.Sample(Path('x.tsv'), {'group_weights': {'kuznetsova': 1.0}}, [
            {'guess': 'пчела', 'target': 'пчелиный', 'group': 'kuznetsova', 'label': 'OK'},
            {'guess': 'вода', 'target': 'водить', 'group': 'kuznetsova', 'label': 'BAD'},
            {'guess': 'пчела', 'target': 'пчеловод', 'group': 'kuznetsova', 'label': ''},
        ])
        item = m.score(sample)['kuznetsova']
        self.assertEqual((item.labeled, item.ok, item.open_now, item.bad_fixed), (2, 1, 1, 1))
        self.assertEqual(m.weighted_precision({'kuznetsova': item}, sample.meta['group_weights'], now=True), 1.0)

    def test_committed_samples_have_known_labels(self):
        for sample in m.load_samples():
            with self.subTest(sample=sample.path.name):
                self.assertTrue(sample.meta.get('group_weights'))
                labels = {(row.get('label') or '').upper() for row in sample.rows}
                self.assertLessEqual(labels, {'OK', 'BAD', ''})
