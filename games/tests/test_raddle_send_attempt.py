"""Интеграционные тесты POST /send_attempt/ для raddle — контракт JSON-ответа."""
import json
from unittest.mock import patch

from django.test import Client, TestCase

from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)

MINI_LADDER = {
    'lengths': [3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD'],
}


def _ensure_fixtures():
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='raddle')


class RaddleSendAttemptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='raddle_send_test',
                name='Raddle send',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
            )
            cls.tg = TaskGroup.objects.create(label='raddle_send_tg')
            GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.tg, number=1, name='L1',
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(MINI_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD',
            )
        cls.anon_key = 'raddle-send-anon'
        cls.post_url = '/send_attempt/{}/'.format(cls.task.id)

    def setUp(self):
        self.client = Client()
        self.client.cookies['interoves_anon'] = self.anon_key

    def _post_word(self, word_index, word):
        return self.client.post(
            self.post_url,
            {
                'game_id': self.game.id,
                'anon_key': self.anon_key,
                'word_index': word_index,
                'word': word,
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_correct_response_contract(self):
        resp = self._post_word(1, 'BBB')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(data['raddle_correct'])
        self.assertEqual(data['raddle_word_index'], 1)
        self.assertNotIn('raddle_needs_sync', data)
        self.assertIn('update_task_html_new', data)

    def test_wrong_response_contract(self):
        resp = self._post_word(1, 'ZZZ')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['raddle_correct'])
        self.assertNotIn('raddle_needs_sync', data)
        self.assertEqual(data['raddle_word_index'], 1)

    def test_duplicate_unsolved_contract(self):
        first = self._post_word(1, 'ZZZ')
        self.assertEqual(first.json()['status'], 'ok')
        self.assertFalse(first.json()['raddle_correct'])

        second = self._post_word(1, 'ZZZ')
        self.assertEqual(second.status_code, 200)
        data = second.json()
        self.assertEqual(data['status'], 'duplicate')
        self.assertFalse(data['raddle_duplicate_solved'])
        self.assertEqual(data['raddle_word_index'], 1)
        self.assertNotIn('update_task_html_new', data)

    def test_duplicate_after_correct_is_solved_sync(self):
        first = self._post_word(1, 'BBB')
        self.assertTrue(first.json()['raddle_correct'])

        second = self._post_word(1, 'BBB')
        data = second.json()
        self.assertEqual(data['status'], 'duplicate')
        self.assertTrue(data['raddle_duplicate_solved'])
        self.assertEqual(data['raddle_word_index'], 1)
        self.assertIn('update_task_html_new', data)

    def test_already_solved_retry_needs_sync_not_correct(self):
        self._post_word(1, 'BBB')
        # Другое слово по уже решённому индексу — не duplicate, но needs_sync.
        resp = self._post_word(1, 'ZZZ')
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['raddle_correct'])
        self.assertTrue(data['raddle_needs_sync'])
        self.assertEqual(data['raddle_word_index'], 1)

    def test_solved_word_persisted_in_chain_state(self):
        self._post_word(1, 'BBB')
        row = ChainTaskState.objects.get(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            game_mode='general',
        )
        state = json.loads(row.state)
        self.assertIn(1, state['solved_indices'])

    def test_wrong_attempt_saved_once(self):
        self._post_word(1, 'ZZZ')
        self._post_word(1, 'ZZZ')
        attempts = Attempt.manager.filter(
            task=self.task, anon_key=self.anon_key, game=self.game,
        )
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.get().status, 'Wrong')
