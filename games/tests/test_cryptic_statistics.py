"""Статистика криптика: попытки, подсказки и популярные неверные ответы."""

from __future__ import annotations

from django.core.cache import caches
from django.test import Client, SimpleTestCase, TestCase

from games.console.games.simple_daily_sections import console_for
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID
from games.models import Task
from games.puzzles.core.check_runtime import KEEP_GOING_STATE
from games.tests.test_advent import _advent_day, _advent_game, _cryptic_game, _user
from games.tests.test_cryptic_hints import CLUE, FORMULA, MARKUP

ANSWER = 'ЖЕМЧУЖИНА'


class CrypticStatisticsTests(TestCase):
    def setUp(self):
        caches['daily_statistics'].clear()
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        item = console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={
            'clue': CLUE, 'answer': ANSWER, 'markup': MARKUP, 'formula': FORMULA,
        })
        self.task = Task.objects.get(task_group_id=item['task_group_id'])

    def _play(self, name, *, wrong=(), hints=(), solve=True):
        client = Client()
        client.force_login(_user(name))
        for number in hints:
            client.post('/send_hint_attempt/{}/'.format(self.task.pk), {'hint_number': number, 'game_id': 'cryptic'})
        for text in wrong:
            client.post('/send_attempt/{}/'.format(self.task.pk), {'text': text, 'game_id': 'cryptic'})
        if solve:
            client.post('/send_attempt/{}/'.format(self.task.pk), {'text': ANSWER.lower(), 'game_id': 'cryptic'})
        return client

    def test_summary_distribution_and_wrong_answers(self):
        for index in range(5):
            self._play('wrong{}'.format(index), wrong=['сокровище' if index % 2 else 'Сокровище'])
        self._play('hinted', hints=('1',))
        self._play('quitter', wrong=['СОКРОВИЩЕ'], solve=False)
        me = self._play('clean')

        data = me.get('/daily-statistics/cryptic/1/').json()
        self.assertEqual(data['kind'], 'cryptic')
        summary = data['summary']
        self.assertEqual(summary['solved'], 7)
        self.assertEqual(summary['median_attempts'], 2)
        self.assertEqual(summary['first_try_percent'], 28.6)
        self.assertEqual(summary['without_hints_percent'], 85.7)
        self.assertEqual(
            [(row['label'], row['count']) for row in data['distribution'] if row['count']],
            [('1', 2), ('2', 5)],
        )
        self.assertEqual(data['distribution'][-1]['label'], '10+')
        # Only solvers count; spellings of one answer are one row.
        self.assertEqual(data['guesses'], [{'word': 'СОКРОВИЩЕ', 'players': 5}])
        self.assertEqual(data['comparison']['attempts']['value'], 1)
        self.assertTrue(data['comparison']['attempts']['perfect'])
        self.assertIn('perfect', [item['code'] for item in data['achievements']])
        self.assertNotIn('_ranking', data)

    def test_first_try_with_a_hint_is_not_perfect(self):
        me = self._play('hinted', hints=('1',))
        attempts = me.get('/daily-statistics/cryptic/1/').json()['comparison']['attempts']
        self.assertEqual(attempts['value'], 1)
        self.assertFalse(attempts['perfect'])

    def test_closed_until_solved(self):
        client = self._play('quitter', wrong=['сокровище'], solve=False)
        self.assertEqual(client.get('/daily-statistics/cryptic/1/').status_code, 403)

    def test_solved_card_holds_the_statistics_block(self):
        page = self._play('clean').get('/cryptic/1/')
        self.assertContains(page, 'data-statistics-url="/daily-statistics/cryptic/1/"')
        self.assertContains(page, 'js/daily_statistics.js')


class AdventStatisticsTests(TestCase):
    """The advent hunt reuses the cryptic statistics; it has no hints."""

    def setUp(self):
        caches['daily_statistics'].clear()
        _advent_game(start='2020-12-01T00:00:00+03:00')
        item = _advent_day(1, answers='ёлка', keep_going='шишка')
        self.task = Task.objects.get(task_group_id=item['task_group_id'])

    def _play(self, name, *, guesses=()):
        client = Client()
        client.force_login(_user(name))
        for text in list(guesses) + ['ёлка']:
            client.post('/send_attempt/{}/'.format(self.task.pk), {'text': text, 'game_id': 'advent'})
        return client

    def test_statistics_and_result_block(self):
        for index in range(5):
            # «шишка» is a «keep going» answer, not a wrong one.
            stepper = self._play('wrong{}'.format(index), guesses=['сосна', 'шишка'])
        me = self._play('first')

        data = me.get('/daily-statistics/advent/1/').json()
        self.assertEqual(data['kind'], 'advent')
        self.assertEqual(data['summary']['solved'], 6)
        self.assertEqual(data['summary']['first_try_percent'], 16.7)
        self.assertNotIn('without_hints_percent', data['summary'])
        self.assertEqual(data['guesses'], [{'word': 'СОСНА', 'players': 5}])
        # сосна, шишка, ёлка: «шишка» is not an attempt.
        self.assertEqual(data['summary']['median_attempts'], 2)
        self.assertTrue(data['comparison']['attempts']['perfect'])

        page = me.get('/advent/1/')
        self.assertContains(page, 'Ваш результат')
        self.assertContains(page, 'data-statistics-url="/daily-statistics/advent/1/"')
        self.assertContains(page, '🔄 1 попытка')
        self.assertContains(page, 'data-share-copy-image')
        self.assertNotContains(page, 'Взят')
        self.assertContains(stepper.get('/advent/1/'), '🔄 2 попытки')


class SingleAnswerAttemptCountTests(SimpleTestCase):
    def test_counts_verdicts_through_the_first_solve(self):
        from types import SimpleNamespace

        from games.daily.timing.statistics import single_answer_attempt_count

        rows = [SimpleNamespace(status=s, state=None) for s in ('Wrong', 'Pending', 'Partial', 'Ok', 'Wrong')]
        self.assertEqual(single_answer_attempt_count(rows), 3)
        self.assertIsNone(single_answer_attempt_count(rows[:2]))
        # An advent «keep going» answer is a step, not a guess.
        step = SimpleNamespace(status='Wrong', state=KEEP_GOING_STATE)
        self.assertEqual(single_answer_attempt_count([step] + rows), 3)
