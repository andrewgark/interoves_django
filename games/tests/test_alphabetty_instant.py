"""Алфавитка с мгновенной победой: хэш загаданного слова и сверка вердикта страницы."""
import json
from unittest.mock import patch

from django.core.cache import caches
from django.test import Client, TestCase, override_settings

from games.daily.catalog.alphabetty import ALPHABETTY_PUBLISH_START_TAG
from games.models import CheckerType, GameTaskGroup, OfflinePlayRule, Task, TaskGroup
from games.ops.analytics.identity import attach_anon_cookie
from games.puzzles.alphabetty.dicts import invalidate_approved_extras
from games.puzzles.alphabetty.play import winning_word_spec
from games.puzzles.core.client_spec import verify
from games.tests.test_alphabetty import _ensure_alphabetty_game

ANON = 'alphabetty-instant-anon'


@override_settings(OFFLINE_PLAY_ENABLED=True)
class AlphabettyWinningWordTests(TestCase):
    def setUp(self):
        caches['offline_play'].clear()
        self.game = _ensure_alphabetty_game(**{ALPHABETTY_PUBLISH_START_TAG: '2020-01-01T00:00:00+03:00'})
        checker = CheckerType.objects.get(id='alphabetty')
        tg = TaskGroup.objects.create(label='alphabetty:1', checker=checker, points=10)
        self.task = Task.objects.create(
            task_group=tg, number='1', task_type='alphabetty', checker=checker,
            checker_data='ЁЖИК', answer='ЁЖИК', points=10,
        )
        GameTaskGroup.objects.create(game=self.game, task_group=tg, number='1', name='Алфавитка #1')
        self.client = Client()
        attach_anon_cookie(self.client, ANON)

    def tearDown(self):
        invalidate_approved_extras()

    def _rule(self):
        OfflinePlayRule.objects.create(task_type='alphabetty', instant_verdict=True)

    def _state(self):
        return self.client.get('/alphabetty/1/state/?anon=' + ANON, HTTP_X_INTEROVES_ANON=ANON).json()

    def _guess(self, word, **extra):
        return self.client.post(
            '/alphabetty/1/guess/', data=json.dumps({'word': word, 'anon_key': ANON, **extra}),
            content_type='application/json', HTTP_X_INTEROVES_ANON=ANON,
        ).json()

    def test_spec_knows_only_the_secret_as_the_server_normalizes_it(self):
        spec = winning_word_spec(self.task)
        self.assertEqual(verify(spec, ' ёжик '), 'Ok')
        self.assertEqual(verify(spec, 'ЕЖИК'), 'Ok')
        self.assertEqual(verify(spec, 'ЁЖ'), 'Wrong')
        self.assertNotIn('ЕЖИК', json.dumps(spec, ensure_ascii=False).upper())

    def test_spec_reaches_the_page_only_with_a_rule(self):
        self.assertIsNone(self._state()['instant_spec'])
        self.assertIsNone(self._guess('год')['instant_spec'])
        self._rule()
        self.assertEqual(self._state()['instant_spec'], winning_word_spec(self.task))
        self.assertEqual(self._guess('яблоко')['instant_spec'], winning_word_spec(self.task))

    @override_settings(OFFLINE_PLAY_ENABLED=False)
    def test_kill_switch_hides_the_spec(self):
        self._rule()
        self.assertIsNone(self._state()['instant_spec'])

    @patch('games.ops.telegram.bot.notify.notify_admin_offline_play')
    def test_a_confirmed_win_is_quiet(self, notify):
        self._rule()
        data = self._guess('ежик', client_verdict='Ok')
        self.assertEqual(data['status'], 'correct')
        self.assertTrue(data['won'])
        notify.assert_not_called()

    @patch('games.ops.telegram.bot.notify.notify_admin_offline_play')
    def test_a_win_the_server_did_not_record_is_reported(self, notify):
        self._rule()
        with self.assertLogs('games.offline', 'WARNING') as logs:
            data = self._guess('год', client_verdict='Ok')
        self.assertFalse(data['won'])
        self.assertIn('offline_verdict_divergence', ' '.join(logs.output))
        self.assertEqual(notify.call_args.kwargs['kind'], 'verdict_divergence')
