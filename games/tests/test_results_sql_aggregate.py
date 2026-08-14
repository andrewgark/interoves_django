"""SQL results aggregate: parity with ORM bulk + snapshot wiring."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from datetime import timedelta

from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    Hint,
    HintAttempt,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.results_snapshot import build_results_snapshot_payload
from games.results_sql_aggregate import (
    get_sql_aggregated_game_actor_rows,
    tasks_need_orm_results_aggregate,
)


def _ensure_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


def _actor_id(actor):
    from games.models import PersonalResultsParticipant, Team as TeamModel
    if isinstance(actor, TeamModel):
        return ('team', actor.pk)
    if isinstance(actor, PersonalResultsParticipant):
        if actor.user_id is not None:
            return ('user', actor.user_id)
        return ('anon', actor.anon_key)
    return ('?', str(actor))


def _normalize_actor_rows(rows_by_task):
    out = {}
    for task_id, rows in rows_by_task.items():
        cells = []
        for actor, ai in rows:
            cells.append({
                'actor': _actor_id(actor),
                'n_attempts': ai.get_n_attempts(),
                'result_points': float(ai.get_result_points()),
                'sum_hint_penalty': float(ai.get_sum_hint_penalty()),
                'best_points': float(ai.best_attempt.points) if ai.best_attempt else None,
                'best_status': ai.best_attempt.status if ai.best_attempt else None,
                'hint_numbers': [
                    ha.hint.number
                    for ha in sorted(
                        [ha for ha in (ai.hint_attempts or []) if ha.is_real_request],
                        key=lambda ha: ha.hint.key_sort(),
                    )
                ],
            })
        cells.sort(key=lambda c: (c['actor'][0], str(c['actor'][1])))
        out[task_id] = cells
    return out


def _normalize_payload(payload):
    rows = []
    for row in payload['rows']:
        rows.append({
            'id': (row['row_kind'], row.get('team_id'), row.get('user_id'), row.get('anon_key')),
            'score': row['score'],
            'place': row['place'],
            'cells': [
                None if c is None else {
                    'n_attempts': c['n_attempts'],
                    'result_points': c['result_points'],
                    'sum_hint_penalty': c['sum_hint_penalty'],
                    'hint_numbers': list(c['hint_numbers']),
                    'cls': c['cls'],
                    'best_status': c.get('best_status'),
                }
                for c in row['cells']
            ],
        })
    rows.sort(key=lambda r: (r['id'][0], str(r['id'][1]), str(r['id'][2]), str(r['id'][3])))
    return {'task_ids': list(payload['task_ids']), 'rows': rows}


class ResultsSqlAggregateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        cls.game = Game.objects.create(
            id='sql_agg_game',
            name='g',
            author='a',
            author_extra='',
            is_ready=True,
        )
        cls.tg = TaskGroup.objects.create(label='tg_sql_agg')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='tg')
        with patch('games.views.track.track_task_change'):
            cls.task1 = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=10,
                checker_data='a',
                text='t1',
            )
            cls.task2 = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='raddle',
                points=10,
                checker_data='b',
                text='t2',
            )
            cls.task3 = Task.objects.create(
                task_group=cls.tg,
                number='3',
                task_type='alphabetty',
                points=10,
                checker_data='c',
                text='t3',
            )
            cls.hint_real = Hint.objects.create(
                task=cls.task2, number='1', points_penalty=2, desc='manual',
            )
            cls.hint_assist = Hint.objects.create(
                task=cls.task2, number='2', points_penalty=5, desc='raddle_clue:1',
            )

        cls.team = Team.objects.create(name='sql_agg_team', visible_name='Team A')
        cls.user = User.objects.create_user(username='sql_agg_user', password='x')
        now = timezone.now()
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(
                text='a', status='Ok', points=10, time=now,
                task=cls.task1, team=cls.team, game=cls.game,
            )
            Attempt.manager.create(
                text='b', status='Partial', points=5, time=now,
                task=cls.task2, team=cls.team, game=cls.game,
            )
            Attempt.manager.create(
                text='a', status='Ok', points=10, time=now,
                task=cls.task1, user=cls.user, team=None, game=cls.game,
            )
            Attempt.manager.create(
                text='wrong', status='Wrong', points=0, time=now,
                task=cls.task2, anon_key='sql-agg-anon', team=None, user=None, game=cls.game,
            )
            alphabetty_attempt = Attempt.manager.create(
                text='c', status='Ok', points=10, time=now,
                task=cls.task3, team=cls.team, game=cls.game,
            )
            # Same points, worse then better status — best should be Ok
            Attempt.manager.create(
                text='early', status='Wrong', points=7, time=now,
                task=cls.task2, user=cls.user, team=None, game=cls.game,
            )
            Attempt.manager.create(
                text='later', status='Ok', points=7, time=now + timedelta(seconds=1),
                task=cls.task2, user=cls.user, team=None, game=cls.game,
            )
            HintAttempt.objects.create(
                team=cls.team, hint=cls.hint_real, is_real_request=True, time=now,
            )
            HintAttempt.objects.create(
                team=cls.team, hint=cls.hint_assist, is_real_request=True, time=now,
            )
            ChainTaskState.objects.create(
                task=cls.task3,
                game=cls.game,
                game_mode='general',
                team=cls.team,
                state=json.dumps({
                    'guesses': ['a', 'c'],
                    'won': True,
                    'hint_prefix': 'ab',
                    'hints_taken': 2,
                }),
                last_attempt=alphabetty_attempt,
            )

    def test_sql_matches_orm_bulk(self):
        task_ids = [self.task1.id, self.task2.id, self.task3.id]
        orm = Attempt.manager.get_bulk_game_actor_rows(
            task_ids, mode='general', game=self.game,
        )
        sql = get_sql_aggregated_game_actor_rows(task_ids, game=self.game)
        self.assertEqual(_normalize_actor_rows(orm), _normalize_actor_rows(sql))

    def test_alphabetty_letter_hint_penalty_is_bulk_aggregated(self):
        sql = get_sql_aggregated_game_actor_rows([self.task3.id], game=self.game)
        team_row = next(ai for actor, ai in sql[self.task3.id] if actor.pk == self.team.pk)
        self.assertEqual(float(team_row.get_sum_hint_penalty()), 2.0)
        self.assertEqual(float(team_row.get_result_points()), 8.0)
        self.assertFalse(tasks_need_orm_results_aggregate([self.task3]))

    def test_assist_hint_in_numbers_not_penalty(self):
        sql = get_sql_aggregated_game_actor_rows([self.task2.id], game=self.game)
        team_row = next(ai for actor, ai in sql[self.task2.id] if actor.pk == self.team.pk)
        self.assertEqual(float(team_row.get_sum_hint_penalty()), 2.0)
        nums = [ha.hint.number for ha in team_row.hint_attempts]
        self.assertEqual(sorted(nums), ['1', '2'])

    def test_snapshot_general_uses_sql_not_bulk(self):
        with patch(
            'games.results_sql_aggregate.get_sql_aggregated_game_actor_rows',
            wraps=get_sql_aggregated_game_actor_rows,
        ) as sql_mock:
            with patch(
                'games.results_snapshot.Attempt.manager.get_bulk_game_actor_rows',
                wraps=Attempt.manager.get_bulk_game_actor_rows,
            ) as bulk_mock:
                payload = build_results_snapshot_payload(self.game, mode='general')
        self.assertEqual(sql_mock.call_count, 1)
        self.assertEqual(bulk_mock.call_count, 0)
        self.assertEqual(len(payload['rows']), 3)

    def test_snapshot_sql_matches_orm_forced(self):
        with patch(
            'games.results_sql_aggregate.tasks_need_orm_results_aggregate',
            return_value=True,
        ):
            orm_payload = build_results_snapshot_payload(self.game, mode='general')
        sql_payload = build_results_snapshot_payload(self.game, mode='general')
        self.assertEqual(_normalize_payload(sql_payload), _normalize_payload(orm_payload))
