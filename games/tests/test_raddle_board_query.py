"""Ladder board reads one actor and does not add queries."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    RaddleUiState,
    Task,
    TaskGroup,
)
from games.puzzles.raddle import RaddlePuzzle
from games.tasks.actor_state import chain_state_for_actor
from games.tasks.result_details import raddle_ui_state_for_actor

MINI_LADDER = {
    'lengths': [3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD'],
}


class LadderBoardQueryTests(TestCase):
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
                id='raddle_board_query',
                name='Board query',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
                is_tournament=False,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
            )
            group = TaskGroup.objects.create(label='raddle_board_query')
            GameTaskGroup.objects.create(game=cls.game, task_group=group, number=1, name='L1')
            cls.task = Task.objects.create(
                task_group=group,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=json.dumps(MINI_LADDER),
                answer='AAA\nBBB\nCCC\nDDD',
            )
            other_group = TaskGroup.objects.create(label='raddle_board_query_other')
            cls.other_task = Task.objects.create(
                task_group=other_group,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=json.dumps(MINI_LADDER),
                answer='AAA\nBBB\nCCC\nDDD',
            )
        cls.anon = 'board-query-mine'
        cls.other = 'board-query-other'
        ChainTaskState.objects.create(
            anon_key=cls.anon, task=cls.task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [1]}),
        )
        ChainTaskState.objects.create(
            anon_key=cls.other, task=cls.task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [2]}),
        )
        ChainTaskState.objects.create(
            anon_key=cls.anon, task=cls.other_task, game=cls.game, game_mode='general',
            state=json.dumps({'solved_indices': [3]}),
        )
        Attempt.manager.create(
            anon_key=cls.anon, task=cls.task, game=cls.game, text='mine', status='Ok',
            state=json.dumps({'solved_indices': [1]}),
        )
        Attempt.manager.create(
            anon_key=cls.other, task=cls.task, game=cls.game, text='theirs', status='Ok',
        )
        RaddleUiState.objects.create(
            anon_key=cls.anon, task=cls.task, game=cls.game, game_mode='general',
            drafts={'2': 'mine'}, clue_marks={},
        )
        RaddleUiState.objects.create(
            anon_key=cls.other, task=cls.task, game=cls.game, game_mode='general',
            drafts={'2': 'theirs'}, clue_marks={},
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
            Attempt.manager.get_attempts(None, self.task, user=None, anon_key=self.anon, game=self.game)
            Attempt.manager.get_hint_attempts(None, self.task, user=None, anon_key=self.anon, game=self.game)
            raddle_ui_state_for_actor(**kwargs)

        with CaptureQueriesContext(connection) as after:
            board = RaddlePuzzle.load_board(**kwargs)

        self.assertEqual(len(after), len(before))
        self.assertEqual(json.loads(board.chain.state)['solved_indices'], [1])
        self.assertEqual([item.text for item in board.attempts], ['mine'])
        self.assertEqual(board.ui_state.drafts, {'2': 'mine'})
        self.assertEqual(board.hint_attempts, [])
        for query in after:
            self.assertNotIn(self.other, query['sql'])

    def test_prepare_reuses_preloaded_attempts(self):
        mine = Attempt.manager.get_attempts(
            None, self.task, user=None, anon_key=self.anon, game=self.game,
        )
        preloaded = SimpleNamespace(attempts=mine, hint_attempts=[])
        with CaptureQueriesContext(connection) as captured:
            data = RaddlePuzzle.prepare_board(
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
                raddle_ui_state_for_actor=None,
                share_title=None,
            )
        self.assertEqual(len(captured), 2)
        self.assertTrue(data['ui']['rows'][1]['is_solved'])
        self.assertFalse(data['ui']['rows'][2]['is_solved'])
        self.assertEqual(data['ui']['rows'][2]['draft_letters'], 'mine')
        for query in captured:
            self.assertNotIn(self.other, query['sql'])
            self.assertNotIn('games_attempt', query['sql'])
