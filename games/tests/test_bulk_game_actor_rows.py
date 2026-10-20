"""Characterization of ``Attempt.manager.get_bulk_game_actor_rows`` scoring.

Pins points / hint numbers / result attempt for alphabetty, raddle and
word_salad cells (team / user / anon actors) and the query count of loading
the rows plus scoring every cell.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    GameTaskGroup,
    HiddenAnonKey,
    Hint,
    HintAttempt,
    PersonalResultsParticipant,
    ReplaySlot,
    Task,
    TaskGroup,
    Team,
)
from games.tests.test_results_sql_aggregate import _ensure_fixtures

WORD_SALAD_CHECKER = json.dumps({
    'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
    'words': ['ABCDEFGHIJKLMNOP', 'ABCD'],
})
ACTOR_KINDS = ('team', 'user', 'anon')


def build_scoring_fixture(prefix, n_actors):
    """Game with alphabetty + raddle + word_salad tasks and ``n_actors`` actors.

    Actor ``i`` is a team / user / anon (cycling) and gets:
      alphabetty: Ok 10 points, ChainTaskState with ``i % 3`` letter hints;
      raddle: Wrong 0 then Partial 5; even ``i`` takes a real hint (penalty 2)
        and a raddle assist (penalty 5, not counted); everyone has a
        non-real request (ignored);
      word_salad: two attempts, the last with 2 solved words and ``i % 2``
        letter hints on word 0.
    """
    game = Game.objects.create(id=prefix + '_game', name='g', author='a', author_extra='', is_ready=True)
    task_group = TaskGroup.objects.create(label=prefix + '_tg')
    GameTaskGroup.objects.create(game=game, task_group=task_group, number=1, name='tg')
    with patch('games.views.track.track_task_change'):
        alphabetty = Task.objects.create(
            task_group=task_group, number='1', task_type='alphabetty', points=10, checker_data='abc', text='a',
        )
        raddle = Task.objects.create(
            task_group=task_group, number='2', task_type='raddle', points=10, checker_data='b', text='r',
        )
        word_salad = Task.objects.create(
            task_group=task_group, number='3', task_type='word_salad', points=2,
            checker_data=WORD_SALAD_CHECKER, text='w',
        )
        hint_real = Hint.objects.create(task=raddle, number='1', points_penalty=2, desc='manual')
        hint_assist = Hint.objects.create(task=raddle, number='2', points_penalty=5, desc='raddle_clue:1')
        hint_unrequested = Hint.objects.create(task=raddle, number='3', points_penalty=1, desc='manual')

    actors = []
    with patch('games.views.track.track_task_change'):
        for i in range(n_actors):
            kind = ACTOR_KINDS[i % 3]
            if kind == 'team':
                actor = {'team': Team.objects.create(name='{}_team_{}'.format(prefix, i), visible_name='T{}'.format(i))}
            elif kind == 'user':
                actor = {'user': User.objects.create_user(username='{}_user_{}'.format(prefix, i), password='x')}
            else:
                actor = {'anon_key': '{}-anon-{}'.format(prefix, i)}
            actors.append((kind, actor))
            create_actor_cells(game, alphabetty, raddle, word_salad, actor, i,
                               hints=(hint_real, hint_assist, hint_unrequested))
    return {
        'game': game,
        'task_group': task_group,
        'alphabetty': alphabetty,
        'raddle': raddle,
        'word_salad': word_salad,
        'task_ids': [alphabetty.pk, raddle.pk, word_salad.pk],
        'actors': actors,
    }


def create_actor_cells(game, alphabetty, raddle, word_salad, actor, i, *, hints):
    hint_real, hint_assist, hint_unrequested = hints
    Attempt.manager.create(text='abc', status='Ok', points=10, task=alphabetty, game=game, **actor)
    ChainTaskState.objects.create(
        task=alphabetty, game=game, game_mode='general',
        state=json.dumps({'guesses': ['abc'], 'won': True, 'hints_taken': i % 3}),
        **actor,
    )
    Attempt.manager.create(text='x', status='Wrong', points=0, task=raddle, game=game, **actor)
    Attempt.manager.create(text='b', status='Partial', points=5, task=raddle, game=game, **actor)
    if i % 2 == 0:
        HintAttempt.objects.create(hint=hint_real, is_real_request=True, **actor)
        HintAttempt.objects.create(hint=hint_assist, is_real_request=True, **actor)
    HintAttempt.objects.create(hint=hint_unrequested, is_real_request=False, **actor)
    Attempt.manager.create(
        text='', status='Partial', points=0, task=word_salad, game=game,
        state=json.dumps({'solved_indices': [0]}), **actor,
    )
    Attempt.manager.create(
        text='', status='Partial', points=0, task=word_salad, game=game,
        state=json.dumps({'solved_indices': [0, 1], 'hint_counts': {'0': i % 2}}), **actor,
    )


def expected_cell(task_type, i):
    """(result_points, hint_numbers) for actor ``i`` of ``build_scoring_fixture``."""
    if task_type == 'alphabetty':
        return Decimal(10 - i % 3), []
    if task_type == 'raddle':
        return (Decimal(3), ['1', '2']) if i % 2 == 0 else (Decimal(5), [])
    # Two words solved, half a point per letter hint; word 0 is displayed second.
    return (Decimal('1.5'), ['2.1']) if i % 2 else (Decimal(2), [])


def score_all_rows(rows_by_task):
    out = {}
    for task_id, rows in rows_by_task.items():
        out[task_id] = [
            (actor, ai.get_result_points(), ai.get_hint_numbers(), ai.get_result_attempt())
            for actor, ai in rows
        ]
    return out


def actor_identity(actor):
    if isinstance(actor, Team):
        return ('team', actor.pk)
    if isinstance(actor, PersonalResultsParticipant):
        if actor.user_id is not None:
            return ('user', actor.user_id)
        return ('anon', actor.anon_key)
    raise AssertionError(actor)


def fixture_identity(kind, actor):
    if kind == 'team':
        return ('team', actor['team'].pk)
    if kind == 'user':
        return ('user', actor['user'].pk)
    return ('anon', actor['anon_key'])


class BulkGameActorRowsCharacterizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()

    def assert_scores_match_fixture(self, fx, scored):
        index_by_identity = {fixture_identity(kind, actor): i for i, (kind, actor) in enumerate(fx['actors'])}
        for task_key in ('alphabetty', 'raddle', 'word_salad'):
            task = fx[task_key]
            cells = scored[task.pk]
            self.assertEqual(
                sorted(actor_identity(actor) for actor, *_ in cells),
                sorted(index_by_identity),
            )
            for actor, points, hint_numbers, result_attempt in cells:
                i = index_by_identity[actor_identity(actor)]
                self.assertEqual((points, hint_numbers), expected_cell(task_key, i), (task_key, i))
                actor_attempts = Attempt.manager.filter(
                    task=task, **fx['actors'][i][1]
                ).order_by('time', 'pk')
                if task_key == 'raddle':
                    expected_attempt = actor_attempts.get(status='Partial')
                else:
                    # alphabetty: the single Ok attempt; word_salad: the last attempt.
                    expected_attempt = actor_attempts.last()
                self.assertEqual(result_attempt.pk, expected_attempt.pk)

    def test_one_actor(self):
        fx = build_scoring_fixture('bulk1', 1)
        rows = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        self.assertEqual(list(rows), fx['task_ids'])
        self.assert_scores_match_fixture(fx, score_all_rows(rows))

    def test_five_actors_team_user_anon(self):
        fx = build_scoring_fixture('bulk5', 5)
        rows = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        self.assertEqual(list(rows), fx['task_ids'])
        self.assertEqual({kind for kind, _ in fx['actors']}, set(ACTOR_KINDS))
        self.assert_scores_match_fixture(fx, score_all_rows(rows))
        # Unscoped (game=None) resolves the penalty through the sample attempt's game.
        unscoped = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general')
        self.assert_scores_match_fixture(fx, score_all_rows(unscoped))

    def test_row_order_follows_actor_bucket_set_iteration(self):
        fx = build_scoring_fixture('bulkord', 6)
        rows = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        for task_id in fx['task_ids']:
            # Mirror the production construction exactly: set(dict.keys())
            # and set(dict) presize differently and can iterate differently.
            attempt_buckets = {}
            for a in Attempt.manager.filter(task_id=task_id, game=fx['game']).order_by('time', 'pk'):
                attempt_buckets.setdefault(Attempt.manager._general_results_actor_bucket(a), None)
            hint_buckets = {}
            for ha in HintAttempt.objects.filter(hint__task_id=task_id):
                hint_buckets.setdefault(Attempt.manager._general_results_actor_bucket(ha), None)
            self.assertEqual(
                [actor_identity(actor) for actor, _ in rows[task_id]],
                list(set(attempt_buckets.keys()) | set(hint_buckets.keys())),
            )

    def test_hint_only_actor_scores_zero_without_result_attempt(self):
        fx = build_scoring_fixture('bulkhint', 1)
        hint = Hint.objects.get(task=fx['raddle'], number='1')
        HintAttempt.objects.create(hint=hint, is_real_request=True, anon_key='bulkhint-only')
        rows = score_all_rows(
            Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        )
        only = [cell for cell in rows[fx['raddle'].pk] if actor_identity(cell[0]) == ('anon', 'bulkhint-only')]
        self.assertEqual(len(only), 1)
        _, points, hint_numbers, result_attempt = only[0]
        self.assertEqual((points, hint_numbers, result_attempt), (0, ['1'], None))

    def test_hidden_team_and_anon_filtered_unless_include_hidden(self):
        fx = build_scoring_fixture('bulkhid', 3)
        team = fx['actors'][0][1]['team']
        team.is_hidden = True
        team.save(update_fields=['is_hidden'])
        HiddenAnonKey.objects.create(anon_key=fx['actors'][2][1]['anon_key'])
        visible = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        for task_id in fx['task_ids']:
            self.assertEqual(
                [actor_identity(a) for a, _ in visible[task_id]],
                [fixture_identity(*fx['actors'][1])],
            )
        everyone = Attempt.manager.get_bulk_game_actor_rows(
            fx['task_ids'], mode='general', game=fx['game'], include_hidden=True,
        )
        self.assert_scores_match_fixture(fx, score_all_rows(everyone))

    def test_actor_filter_limits_rows_and_keeps_letter_penalty(self):
        fx = build_scoring_fixture('bulkflt', 6)
        # Actor 4 is a user with one alphabetty letter hint.
        kind, actor = fx['actors'][4]
        self.assertEqual(kind, 'user')
        actor_filter = {'team__isnull': True, 'user_id': actor['user'].pk, 'anon_key__isnull': True}
        rows = score_all_rows(Attempt.manager.get_bulk_game_actor_rows(
            fx['task_ids'], mode='general', game=fx['game'], actor_filter=actor_filter, include_hidden=True,
        ))
        for task_key in ('alphabetty', 'raddle', 'word_salad'):
            cells = rows[fx[task_key].pk]
            self.assertEqual([actor_identity(c[0]) for c in cells], [('user', actor['user'].pk)])
            self.assertEqual(tuple(cells[0][1:3]), expected_cell(task_key, 4))

    def test_game_scoping_and_penalty_keyed_by_sample_attempt_game(self):
        fx = build_scoring_fixture('bulkgame', 1)
        other_game = Game.objects.create(id='bulkgame_other', name='g2', author='a', author_extra='', is_ready=True)
        GameTaskGroup.objects.create(game=other_game, task_group=fx['task_group'], number=1, name='tg')
        actor = {'anon_key': 'bulkgame-other-anon'}
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(text='abc', status='Ok', points=10, task=fx['alphabetty'], game=other_game, **actor)
        ChainTaskState.objects.create(
            task=fx['alphabetty'], game=other_game, game_mode='general',
            state=json.dumps({'hints_taken': 2}), **actor,
        )
        # A state in the scoped game for the same actor must not be used.
        ChainTaskState.objects.create(
            task=fx['alphabetty'], game=fx['game'], game_mode='general',
            state=json.dumps({'hints_taken': 7}), **actor,
        )
        scoped = Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game'])
        self.assertNotIn(('anon', actor['anon_key']), {actor_identity(a) for a, _ in scoped[fx['alphabetty'].pk]})
        unscoped = score_all_rows(Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general'))
        cell = [c for c in unscoped[fx['alphabetty'].pk] if actor_identity(c[0]) == ('anon', actor['anon_key'])]
        self.assertEqual([c[1] for c in cell], [Decimal(8)])

    def test_replay_slot_rows_use_official_letter_hint_state(self):
        fx = build_scoring_fixture('bulkreplay', 1)
        user = User.objects.create_user(username='bulkreplay_player', password='x')
        slot = ReplaySlot.objects.create(user=user, game=fx['game'], task_group=fx['task_group'])
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(
                text='abc', status='Ok', points=10, task=fx['alphabetty'], game=fx['game'],
                user=user, replay_slot=slot,
            )
        ChainTaskState.objects.create(
            task=fx['alphabetty'], game=fx['game'], game_mode='general', user=user,
            replay_slot=slot, state=json.dumps({'hints_taken': 3}),
        )
        ChainTaskState.objects.create(
            task=fx['alphabetty'], game=fx['game'], game_mode='general', user=user,
            state=json.dumps({'hints_taken': 1}),
        )
        rows = score_all_rows(Attempt.manager.get_bulk_game_actor_rows(
            fx['task_ids'], mode='general', game=fx['game'], replay_slot=slot,
        ))
        cells = rows[fx['alphabetty'].pk]
        self.assertEqual([actor_identity(c[0]) for c in cells], [('user', user.pk)])
        # Today's behavior: replay rows are penalised by the replay_slot=NULL state.
        self.assertEqual(cells[0][1], Decimal(9))


class BulkGameActorRowsQueryCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()

    def count_queries(self, n_actors):
        fx = build_scoring_fixture('bulkq{}'.format(n_actors), n_actors)
        with CaptureQueriesContext(connection) as ctx:
            score_all_rows(Attempt.manager.get_bulk_game_actor_rows(fx['task_ids'], mode='general', game=fx['game']))
        return len(ctx.captured_queries)

    def test_query_count_is_constant_in_actors(self):
        # hidden anon keys + tasks + attempts + hint attempts + alphabetty
        # letter-hint states; scoring the cells issues no further queries.
        # (Before bulk loading this was 3 + 4 * N.)
        for n_actors in (1, 5, 20):
            with self.subTest(n_actors=n_actors):
                self.assertEqual(self.count_queries(n_actors), 5)

    def test_attempts_info_without_preloaded_penalty_looks_it_up(self):
        fx = build_scoring_fixture('bulkdefault', 3)
        _, actor = fx['actors'][2]
        info = Attempt.manager.get_attempts_info(
            team=None, task=fx['alphabetty'], game=fx['game'], **actor,
        )
        self.assertIsNone(info.letter_hint_penalty)
        self.assertEqual(info.get_result_points(), expected_cell('alphabetty', 2)[0])
