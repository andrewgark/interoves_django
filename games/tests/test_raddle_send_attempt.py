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

# Пять слов: сначала playable только 1 и 3; индекс 2 — «как КАНАВА» до раскрытия края.
LONG_LADDER = {
    'lengths': [3, 3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D', '____ E'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'],
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
        self.assertNotIn('update_task_html_new', data)

    def test_wrong_after_progress_still_not_correct(self):
        """Неверное слово после частичного прогресса — не raddle_correct (status может быть Partial)."""
        self._post_word(1, 'BBB')
        resp = self._post_word(2, 'ZZZ')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['raddle_correct'])
        self.assertNotIn('raddle_needs_sync', data)
        self.assertEqual(data['raddle_word_index'], 2)
        self.assertNotIn('update_task_html_new', data)

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

    def test_premature_correct_word_can_be_resent_when_playable(self):
        """Верное слово, сданное до playable (Wrong), не блокирует повтор — баг КАНАВА."""
        with patch('games.views.track.track_task_change'):
            long_task = Task.objects.create(
                task_group=self.tg,
                number='2',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(LONG_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
            )
        url = '/send_attempt/{}/'.format(long_task.id)
        anon = 'raddle-premature-anon'
        self.client.cookies['interoves_anon'] = anon

        def post(word_index, word):
            return self.client.post(
                url,
                {
                    'game_id': self.game.id,
                    'anon_key': anon,
                    'word_index': word_index,
                    'word': word,
                },
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )

        premature = post(2, 'CCC')
        self.assertEqual(premature.json()['status'], 'ok')
        self.assertFalse(premature.json()['raddle_correct'])

        # ensure_ascii=True в старых посылках — семантический дубликат всё равно узнаём
        Attempt.manager.filter(task=long_task, anon_key=anon).update(
            text=json.dumps({'word_index': 2, 'word': 'CCC'}),  # default ensure_ascii=True
        )

        open_edge = post(1, 'BBB')
        self.assertTrue(open_edge.json()['raddle_correct'])

        again = post(2, 'CCC')
        data = again.json()
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(data['raddle_correct'])
        self.assertEqual(data['raddle_word_index'], 2)
        self.assertIn('update_task_html_new', data)

        row = ChainTaskState.objects.get(
            anon_key=anon, task=long_task, game=self.game, game_mode='general',
        )
        self.assertIn(2, json.loads(row.state)['solved_indices'])

    def test_stale_not_playable_syncs_without_saving(self):
        """Устаревшая форма на середине: sync HTML, Attempt не пишется."""
        with patch('games.views.track.track_task_change'):
            long_task = Task.objects.create(
                task_group=self.tg,
                number='3',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(LONG_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
            )
        url = '/send_attempt/{}/'.format(long_task.id)
        anon = 'raddle-stale-anon'
        self.client.cookies['interoves_anon'] = anon

        resp = self.client.post(
            url,
            {
                'game_id': self.game.id,
                'anon_key': anon,
                'word_index': 2,
                'word': 'CCC',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['raddle_correct'])
        self.assertTrue(data['raddle_needs_sync'])
        self.assertTrue(data.get('raddle_stale_ui'))
        self.assertIn('update_task_html_new', data)
        self.assertEqual(
            Attempt.manager.filter(task=long_task, anon_key=anon).count(),
            0,
        )


class RaddleDuplicateHelpersTests(TestCase):
    def test_payloads_match_across_ensure_ascii(self):
        from games.raddle import raddle_attempt_payloads_match, serialize_raddle_attempt_text

        raw_cyr = serialize_raddle_attempt_text(11, 'КАНАВА')
        escaped = json.dumps({'word_index': 11, 'word': 'КАНАВА'})  # ensure_ascii=True
        self.assertNotEqual(raw_cyr, escaped)
        self.assertTrue(raddle_attempt_payloads_match(raw_cyr, escaped))

    def test_blocks_wrong_but_not_premature_correct(self):
        from games.raddle import raddle_blocks_as_duplicate, serialize_raddle_attempt_text

        _ensure_fixtures()
        with patch('games.views.track.track_task_change'):
            game = Game.objects.create(
                id='raddle_dup_helper',
                name='dup',
                author='t',
                author_extra='',
                project_id='sections',
                is_ready=True,
            )
            tg = TaskGroup.objects.create(label='raddle_dup_tg')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=1, name='L')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(LONG_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
            )
        text_correct = serialize_raddle_attempt_text(2, 'CCC')
        text_wrong = serialize_raddle_attempt_text(2, 'ZZZ')
        state = json.dumps({'solved_indices': [0, 4], 'used_hints': [], 'total': 0})
        self.assertFalse(raddle_blocks_as_duplicate(
            text_correct, text_correct, task=task, state_raw=state,
        ))
        self.assertTrue(raddle_blocks_as_duplicate(
            text_wrong, text_wrong, task=task, state_raw=state,
        ))
        state_solved = json.dumps({'solved_indices': [0, 2, 4], 'used_hints': [], 'total': 1})
        self.assertTrue(raddle_blocks_as_duplicate(
            text_correct, text_correct, task=task, state_raw=state_solved,
        ))
