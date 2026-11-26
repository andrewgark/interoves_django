"""Подсказки криптика: черта с определением, затем вся разметка; 3 балла, по −1."""

from __future__ import annotations

from django.test import Client, SimpleTestCase, TestCase

from games.console.games.simple_daily_sections import console_for
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID
from games.models import Attempt, GameTaskGroup, Hint, Task
from games.puzzles.cryptic.explain import hint_htmls
from games.tests.test_advent import _cryptic_game, _user

CLUE = 'Пьяный мужчина же сокровище'
MARKUP = '*Пьяный* (мужчина же) | {сокровище}'
FORMULA = 'анаг[Пьяный](МУЖЧИНА ЖЕ)'


class HintHtmlTests(SimpleTestCase):
    def test_first_shows_only_split_and_definition(self):
        first, second = hint_htmls(clue=CLUE, answer='ЖЕМЧУЖИНА', markup=MARKUP, formula=FORMULA)
        self.assertIn('cx-split', first)
        self.assertIn('cx-seg--def', first)
        self.assertNotIn('cx-seg--ind', first)
        self.assertNotIn('cx-seg--fod', first)
        self.assertIn('мужчина же', first)
        for html in (first, second):
            self.assertNotIn('ЖЕМЧУЖИНА', html)
            self.assertNotIn('cx-steps', html)
            self.assertNotIn('data-cx-ids', html)
        self.assertIn('cx-seg--ind', second)
        self.assertIn('cx-seg--fod', second)
        self.assertIn('анаграмма', second)

    def test_double_definition_marks_only_the_first(self):
        clue = 'Лучше, чтобы не отказывал вам политический институт'
        markup = '{Лучше, чтобы не отказывал вам} | {политический институт}'
        first, second = hint_htmls(clue=clue, answer='ОРГАН', markup=markup, formula='ОРГАН')
        self.assertEqual(first.count('cx-seg--def'), 1)
        self.assertEqual(second.count('cx-seg--def'), 2)

    def test_no_hints_without_a_checked_explanation(self):
        self.assertIsNone(hint_htmls(clue=CLUE, answer='ЖЕМЧУЖИНА', markup='', formula=''))
        self.assertIsNone(hint_htmls(clue=CLUE, answer='ЖЕМЧУЖИНА', markup=MARKUP, formula='обр(X)'))


class CrypticHintPlayTests(TestCase):
    def setUp(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        self.console = console_for(CRYPTIC_GAME_ID)
        self.item = self.console.create(at_number=1, fields={
            'clue': CLUE, 'answer': 'ЖЕМЧУЖИНА', 'markup': MARKUP, 'formula': FORMULA,
        })
        self.task = Task.objects.get(task_group_id=self.item['task_group_id'])

    def test_console_keeps_two_chained_hints(self):
        self.assertEqual(self.task.points, 3)
        self.assertEqual(self.task.task_group.points, 3)
        first, second = sorted(Hint.objects.filter(task=self.task), key=lambda h: h.number)
        self.assertEqual((first.number, second.number), ('1', '2'))
        self.assertEqual((first.points_penalty, second.points_penalty), (1, 1))
        self.assertEqual(list(second.required_hints.all()), [first])
        self.console.update(self.item['link_id'], fields={'markup': '', 'formula': ''})
        self.assertFalse(Hint.objects.filter(task=self.task).exists())

    def test_each_hint_costs_a_point(self):
        client = Client()
        client.force_login(_user('cryptic_hinter'))
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'черта и определение')
        self.assertNotContains(page, 'cx-explain--hint')
        too_early = client.post('/send_hint_attempt/{}/'.format(self.task.pk),
                                {'hint_number': '2', 'game_id': 'cryptic'}).json()
        self.assertEqual(too_early['status'], 'not_all_required_hints_taken')
        for number in ('1', '2'):
            res = client.post('/send_hint_attempt/{}/'.format(self.task.pk),
                              {'hint_number': number, 'game_id': 'cryptic'}).json()
            self.assertEqual(res['status'], 'ok')
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'cx-explain--hint', count=2)
        client.post('/send_attempt/{}/'.format(self.task.pk), {'text': 'жемчужина', 'game_id': 'cryptic'})
        game = GameTaskGroup.objects.get(pk=self.item['link_id']).game
        info = Attempt.manager.get_attempts_info(
            team=None, user=page.wsgi_request.user, task=self.task, mode='general', game=game,
        )
        self.assertEqual(info.get_result_points(), 1)
