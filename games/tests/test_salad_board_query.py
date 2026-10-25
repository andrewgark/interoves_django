"""Salad board reads one actor and does not add queries."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup
from games.puzzles.salad import WordSaladPuzzle
from games.tasks.actor_state import chain_state_for_actor

PUZZLE = {
    'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
    'words': ['ABCD', 'EFGH'],
}


class SaladBoardQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='salad_board_query',
                name='Salad board',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
                is_tournament=False,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
            )
            group = TaskGroup.objects.create(label='salad_board_query')
            GameTaskGroup.objects.create(game=cls.game, task_group=group, number=1, name='S1')
            cls.task = Task.objects.create(
                task_group=group,
                number='1',
                task_type='word_salad',
                points=1,
                checker_data=json.dumps(PUZZLE),
                answer='',
            )
            other_group = TaskGroup.objects.create(label='salad_board_query_other')
            cls.other_task = Task.objects.create(
                task_group=other_group,
                number='1',
                task_type='word_salad',
                points=1,
                checker_data=json.dumps(PUZZLE),
                answer='',
            )
        cls.anon = 'salad-board-mine'
        cls.other = 'salad-board-other'
        ChainTaskState.objects.create(
            anon_key=cls.anon, task=cls.task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [0]}),
        )
        ChainTaskState.objects.create(
            anon_key=cls.other, task=cls.task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [1]}),
        )
        ChainTaskState.objects.create(
            anon_key=cls.anon, task=cls.other_task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [2]}),
        )
        Attempt.manager.create(
            anon_key=cls.anon, task=cls.task, game=cls.game, text='mine', status='Ok',
            state=json.dumps({'solved_indices': [0]}),
        )
        Attempt.manager.create(
            anon_key=cls.other, task=cls.task, game=cls.game, text='theirs', status='Ok',
        )

    def test_full_read_matches_the_separate_lookups(self):
        kwargs = dict(
            game=self.game, task=self.task, team=None, user=None,
            anon_key=self.anon, mode='general', replay_slot=None,
        )
        with CaptureQueriesContext(connection) as before:
            chain_state_for_actor(self.task, self.game, **{
                'team': None, 'user': None, 'anon_key': self.anon,
                'mode': 'general', 'replay_slot': None,
            })
            Attempt.manager.get_attempts(
                None, self.task, user=None, anon_key=self.anon, game=self.game,
            )
        with CaptureQueriesContext(connection) as after:
            board = WordSaladPuzzle.load_board(**kwargs)

        self.assertEqual(len(after), len(before))
        self.assertEqual(json.loads(board.chain.state)['solved_indices'], [0])
        self.assertEqual(board.chain.task_id, self.task.id)
        self.assertEqual([item.text for item in board.attempts], ['mine'])
        for query in after:
            self.assertNotIn(self.other, query['sql'])

    def test_prepare_reuses_preloaded_attempts(self):
        mine = Attempt.manager.get_attempts(
            None, self.task, user=None, anon_key=self.anon, game=self.game,
        )
        preloaded = SimpleNamespace(attempts=mine)
        with CaptureQueriesContext(connection) as captured:
            data = WordSaladPuzzle.prepare_board(
                game=self.game,
                task=self.task,
                placement=None,
                attempts_info=preloaded,
                team=None,
                user=None,
                anon_key=self.anon,
                mode='general',
                replay_slot=None,
                resolve_chain_state=None,
            )
        self.assertEqual(len(captured), 1)
        self.assertEqual(data['solved_indices'], [0])
        for query in captured:
            self.assertNotIn(self.other, query['sql'])
            self.assertNotIn('games_attempt', query['sql'])
