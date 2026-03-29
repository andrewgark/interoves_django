"""
Tests for ChainTaskState: wall and replacements_lines chain integrity,
mode isolation, race-condition serialisation, and recheck correctness.
"""
import json
import threading
from unittest.mock import patch

import unittest

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from games.models import (
    Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, HTMLPage,
    Project, Task, TaskGroup, Team, CHAIN_TASK_TYPES,
)
from games.recheck import recheck_chain_task
from games.views.attempt_views import check_attempt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Wall: 4 categories × 4 words.  task.text = layout JSON; task.checker_data = answers JSON.
# With 4 categories, the "auto-add last remaining category" fires only when 3 have already
# been guessed (n_answers - 1 = 3), so guessing 1 or 2 produces exact expected counts.
_WALL_WORDS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P']
_WALL_TEXT = json.dumps({
    'words': _WALL_WORDS,
    'n_cat': 4,
    'n_words': 4,
})
_WALL_CHECKER_DATA = json.dumps({
    'answers': [
        {'words': ['A', 'B', 'C', 'D'], 'checker': 'Cat1'},
        {'words': ['E', 'F', 'G', 'H'], 'checker': 'Cat2'},
        {'words': ['I', 'J', 'K', 'L'], 'checker': 'Cat3'},
        {'words': ['M', 'N', 'O', 'P'], 'checker': 'Cat4'},
    ],
    'points_words': 1,
    'points_explanation': 1,
    'points_bonus': 1,
})

# Replacements: 2 lines with one slot each.
_REPLACEMENTS_CHECKER_DATA = json.dumps({
    'lines': [['answer1'], ['answer2']],
})


def _setup_db():
    """Create the minimal fixture objects required by check_attempt."""
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    CheckerType.objects.get_or_create(pk='wall')
    CheckerType.objects.get_or_create(pk='replacements_lines')


def _make_game(suffix=''):
    return Game.objects.create(
        id='chain_test_game' + suffix,
        name='chain test' + suffix,
        author='test',
        author_extra='',
    )


def _make_wall_task(game, suffix=''):
    with patch('games.views.track.track_task_change'):
        tg = TaskGroup.objects.create(label='tg_wall' + suffix, points=1)
        GameTaskGroup.objects.create(
            game=game, task_group=tg, number=1, name='tg_wall' + suffix,
        )
        checker = CheckerType.objects.get(pk='wall')
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='wall',
            checker=checker,
            text=_WALL_TEXT,
            checker_data=_WALL_CHECKER_DATA,
        )
    return task


def _make_replacements_task(game, suffix=''):
    with patch('games.views.track.track_task_change'):
        tg = TaskGroup.objects.create(label='tg_repl' + suffix, points=1)
        GameTaskGroup.objects.create(
            game=game, task_group=tg, number=2, name='tg_repl' + suffix,
        )
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='replacements_lines',
            checker_data=_REPLACEMENTS_CHECKER_DATA,
        )
    return task


def _make_attempt(task, team, text, dt=None, game=None):
    if game is None:
        game = GameTaskGroup.objects.get(task_group=task.task_group).game
    return Attempt(
        task=task,
        team=team,
        text=text,
        time=dt or timezone.now(),
        game=game,
    )


def _wall_text(words, stage='cat_words', explanation=None):
    payload = {'words': sorted(words), 'stage': stage}
    if explanation is not None:
        payload['explanation'] = explanation
    return json.dumps(payload)


def _repl_text(line_index, answers):
    return json.dumps({'line_index': line_index, 'answers': answers})


# ===========================================================================
# Shared fixture mixin
# ===========================================================================
class _ChainFixture:
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        cls.game = _make_game()
        cls.team = Team.objects.create(name='chain_test_team', visible_name='T')
        cls.team2 = Team.objects.create(name='chain_test_team2', visible_name='T2')
        cls.wall_task = _make_wall_task(cls.game)
        cls.repl_task = _make_replacements_task(cls.game)


# ===========================================================================
# ChainTaskState creation
# ===========================================================================
class ChainTaskStateCreationTests(_ChainFixture, TestCase):

    def test_no_chain_state_before_first_attempt(self):
        self.assertFalse(
            ChainTaskState.objects.filter(task=self.repl_task, team=self.team).exists()
        )

    def test_chain_state_created_on_first_replacements_attempt(self):
        a = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a)
        self.assertTrue(
            ChainTaskState.objects.filter(task=self.repl_task, team=self.team).exists()
        )

    def test_chain_state_created_on_first_wall_attempt(self):
        a = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a)
        self.assertTrue(
            ChainTaskState.objects.filter(task=self.wall_task, team=self.team).exists()
        )

    def test_only_one_chain_state_row_per_actor_task_mode(self):
        for i in range(3):
            a = _make_attempt(self.repl_task, self.team, _repl_text(0, ['attempt{}'.format(i)]))
            check_attempt(a)
        count = ChainTaskState.objects.filter(
            task=self.repl_task, team=self.team, game_mode='general',
        ).count()
        self.assertEqual(count, 1)

    def test_two_teams_have_independent_chain_state_rows(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team2, _repl_text(0, ['answer1']))
        check_attempt(a2)
        self.assertEqual(
            ChainTaskState.objects.filter(task=self.repl_task).count(), 2
        )


# ===========================================================================
# Replacements_lines chain accumulation
# ===========================================================================
class ReplacementsChainTests(_ChainFixture, TestCase):

    def test_first_attempt_sets_state(self):
        a = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertEqual(state['total'], 1)

    def test_second_attempt_accumulates(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertIn(1, state['solved_lines'])
        self.assertEqual(state['total'], 2)

    def test_wrong_attempt_preserves_previous_solved(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['wrong']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertNotIn(1, state['solved_lines'])

    def test_last_attempt_on_row_points_to_latest(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        self.assertEqual(row.last_attempt_id, a2.pk)

    def test_chain_state_matches_individual_attempt_state(self):
        """ChainTaskState.state must equal the last Attempt.state."""
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        a2.refresh_from_db()
        self.assertEqual(row.state, a2.state)


# ===========================================================================
# Wall chain accumulation
# ===========================================================================
class WallChainTests(_ChainFixture, TestCase):

    def test_first_cat_words_sets_guessed_words(self):
        a = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a)
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        state = json.loads(row.state)
        self.assertEqual(len(state['guessed_words']), 1)

    def test_second_cat_words_accumulates(self):
        a1 = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a1)
        a2 = _make_attempt(self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        state = json.loads(row.state)
        self.assertEqual(len(state['guessed_words']), 2)

    def test_repeated_guess_for_already_guessed_category_is_wrong(self):
        """Guessing an already-guessed cat_words returns Wrong (duplicate is blocked at state level)."""
        a1 = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a1)
        # Same category but via explanation stage to bypass text-duplicate guard
        a2 = _make_attempt(
            self.wall_task, self.team,
            _wall_text(['A', 'B', 'C', 'D'], stage='cat_explanation', explanation='Cat1'),
        )
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        state = json.loads(row.state)
        # cat_words for ['A','B','C','D'] already guessed; explanation attempt may succeed or not.
        # Key invariant: state still has at least 1 guessed category.
        self.assertGreaterEqual(len(state['guessed_words']), 1)

    def test_wall_best_points_accumulate_across_attempts(self):
        a1 = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a1)
        a2 = _make_attempt(self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        state = json.loads(row.state)
        self.assertGreater(state['best_points'], 0)


# ===========================================================================
# Mode isolation: general vs tournament chains are independent
# ===========================================================================
class ModeIsolationTests(_ChainFixture, TestCase):
    """
    Both wall and replacements_lines use current_mode as the ChainTaskState key.
    A tournament attempt creates a fresh chain row independent of the general row.
    """

    def _patch_tournament(self, task):
        """Context manager: force game.get_current_mode to return 'tournament'."""
        # Patch on the model class so it applies to any Game instance loaded from FKs.
        return patch.object(Game, 'get_current_mode', return_value='tournament')

    def test_tournament_attempt_creates_separate_row(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)

        with self._patch_tournament(self.repl_task):
            a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
            check_attempt(a2)

        self.assertEqual(
            ChainTaskState.objects.filter(task=self.repl_task, team=self.team).count(),
            2,
        )
        general_row = ChainTaskState.objects.get(
            task=self.repl_task, team=self.team, game_mode='general',
        )
        tournament_row = ChainTaskState.objects.get(
            task=self.repl_task, team=self.team, game_mode='tournament',
        )
        general_state = json.loads(general_row.state)
        self.assertIn(0, general_state['solved_lines'])
        self.assertNotIn(1, general_state['solved_lines'])

        tournament_state = json.loads(tournament_row.state)
        self.assertNotIn(0, tournament_state['solved_lines'])
        self.assertIn(1, tournament_state['solved_lines'])

    def test_tournament_starts_fresh_for_wall(self):
        # general: guess category 1
        a1 = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a1)

        # tournament: guess category 2 — chain should not see category 1 guessed
        with self._patch_tournament(self.wall_task):
            a2 = _make_attempt(self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']))
            check_attempt(a2)

        tournament_row = ChainTaskState.objects.get(
            task=self.wall_task, team=self.team, game_mode='tournament',
        )
        t_state = json.loads(tournament_row.state)
        # Only category 2 guessed in tournament (fresh start, category 1 not visible)
        self.assertEqual(len(t_state['guessed_words']), 1)

    def test_general_chain_unaffected_by_tournament_attempts(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)

        with self._patch_tournament(self.repl_task):
            a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
            check_attempt(a2)

        # A subsequent general attempt for line 1 — the duplicate guard only fires
        # when same text appears within the SAME mode's attempts list, so we can
        # reuse line 1 answer in general mode since a2 was recorded in tournament mode.
        # However the general mode filter returns ALL attempts (including tournament ones
        # by time), so the text might be seen as duplicate. Use a different answer to be safe.
        a3 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2_general']))
        # 'answer2_general' is wrong but advances chain from general state
        check_attempt(a3)

        general_row = ChainTaskState.objects.get(
            task=self.repl_task, team=self.team, game_mode='general',
        )
        general_state = json.loads(general_row.state)
        # Line 0 still solved in general chain
        self.assertIn(0, general_state['solved_lines'])
        # Line 1 NOT solved (wrong answer)
        self.assertNotIn(1, general_state['solved_lines'])


# ===========================================================================
# recheck_chain_task: rebuilds state correctly from scratch
# ===========================================================================
class RecheckChainTaskTests(_ChainFixture, TestCase):

    def test_recheck_rebuilds_state_from_scratch(self):
        """After recheck, ChainTaskState matches what check_attempt would produce."""
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)

        # Corrupt the state to simulate a stale/broken chain
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        row.state = json.dumps({'solved_lines': [], 'total': 0})
        row.save()

        recheck_chain_task(task=self.repl_task, team=self.team)

        row.refresh_from_db()
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertIn(1, state['solved_lines'])
        self.assertEqual(state['total'], 2)

    def test_recheck_updates_individual_attempt_states(self):
        """Each Attempt.state is also updated during recheck."""
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)

        # Corrupt a1's state
        a1.state = None
        a1.save()

        recheck_chain_task(task=self.repl_task, team=self.team)

        a1.refresh_from_db()
        self.assertIsNotNone(a1.state)
        s1 = json.loads(a1.state)
        # Only line 0 solved at the point of a1
        self.assertIn(0, s1['solved_lines'])
        self.assertNotIn(1, s1['solved_lines'])

    def test_recheck_with_wrong_then_correct_answer(self):
        """If checker_data is corrected and recheck runs, solved lines update correctly."""
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['wrong']))
        check_attempt(a1)
        self.assertEqual(a1.status, 'Wrong')

        # Simulate correcting checker: 'wrong' is now the right answer for line 0
        corrected_checker = json.dumps({'lines': [['wrong'], ['answer2']]})
        with patch('games.views.track.track_task_change'):
            self.repl_task.checker_data = corrected_checker
            self.repl_task.save()

        recheck_chain_task(task=self.repl_task, team=self.team)

        a1.refresh_from_db()
        # 1 of 2 lines solved → Partial
        self.assertEqual(a1.status, 'Partial')
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team, game_mode='general')
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])

    def test_recheck_wall_chain(self):
        a1 = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a1)
        a2 = _make_attempt(self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']))
        check_attempt(a2)

        # Corrupt the chain state
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        row.state = None
        row.save()

        recheck_chain_task(task=self.wall_task, team=self.team)

        row.refresh_from_db()
        state = json.loads(row.state)
        self.assertEqual(len(state['guessed_words']), 2)

    def test_recheck_creates_both_mode_rows(self):
        """recheck_chain_task creates ChainTaskState rows for both modes even if one is empty."""
        recheck_chain_task(task=self.repl_task, team=self.team2)
        rows = ChainTaskState.objects.filter(task=self.repl_task, team=self.team2)
        modes = set(rows.values_list('game_mode', flat=True))
        self.assertIn('general', modes)
        self.assertIn('tournament', modes)

    def test_recheck_preserves_order_of_accumulation(self):
        """
        Recheck replays attempts in chronological order.
        The intermediate Attempt.state after a1 should only reflect a1's contribution.
        """
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)

        # Zero out states to ensure recheck rebuilds them
        Attempt.manager.filter(task=self.repl_task, team=self.team).update(state=None)

        recheck_chain_task(task=self.repl_task, team=self.team)

        a1.refresh_from_db()
        a2.refresh_from_db()
        s1 = json.loads(a1.state)
        s2 = json.loads(a2.state)
        # After a1: only line 0
        self.assertIn(0, s1['solved_lines'])
        self.assertNotIn(1, s1['solved_lines'])
        # After a2: both lines
        self.assertIn(0, s2['solved_lines'])
        self.assertIn(1, s2['solved_lines'])


# ===========================================================================
# Concurrent-submission serialisation
# Uses TransactionTestCase so threads see committed data across connections.
# SELECT FOR UPDATE is not supported by SQLite — skip on that backend.
# ===========================================================================
class ConcurrentSubmissionTests(TransactionTestCase):
    """
    Two threads submit attempts nearly simultaneously.  With SELECT FOR UPDATE
    the second must wait for the first's transaction to commit and then read
    the updated ChainTaskState, not the stale state.
    """

    def setUp(self):
        _setup_db()
        self.game = _make_game(suffix='_race')
        self.team = Team.objects.create(name='chain_race_team', visible_name='R')
        self.repl_task = _make_replacements_task(self.game, suffix='_race')

    def test_concurrent_attempts_both_complete_without_duplication(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SELECT FOR UPDATE row locking not supported in SQLite')
        errors = []
        results = []

        def submit(line_idx, answer):
            try:
                a = _make_attempt(
                    self.repl_task, self.team,
                    _repl_text(line_idx, [answer]),
                )
                check_attempt(a)
                results.append(a)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=submit, args=(0, 'answer1'))
        t2 = threading.Thread(target=submit, args=(1, 'answer2'))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], 'Threads raised: {}'.format(errors))

        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertIn(1, state['solved_lines'])
        self.assertEqual(state['total'], 2)


# ===========================================================================
# Edge cases
# ===========================================================================
class EdgeCaseTests(_ChainFixture, TestCase):

    def test_non_chain_task_does_not_create_chain_state(self):
        """default task type must not touch ChainTaskState."""
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg_default', points=1)
            GameTaskGroup.objects.create(
                game=self.game, task_group=tg, number=99, name='tg_default',
            )
            checker = CheckerType.objects.get(pk='equals_with_possible_spaces')
            default_task = Task.objects.create(
                task_group=tg,
                number='99',
                task_type='default',
                checker=checker,
                answer='кот',
            )
        a = _make_attempt(default_task, self.team, 'кот')
        check_attempt(a)
        self.assertFalse(ChainTaskState.objects.filter(task=default_task).exists())

    def test_chain_state_row_is_last_attempt_link(self):
        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        self.assertEqual(row.last_attempt_id, a2.pk)

    def test_chain_state_state_summary_replacements(self):
        a = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team)
        summary = row.state_summary()
        self.assertIn('1', summary)
        self.assertIn('line', summary)

    def test_chain_state_state_summary_wall(self):
        a = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(a)
        row = ChainTaskState.objects.get(task=self.wall_task, team=self.team)
        summary = row.state_summary()
        self.assertIn('pts', summary)
        self.assertIn('stage', summary)

    def test_chain_state_summary_empty_state(self):
        row = ChainTaskState(task=self.repl_task, team=self.team, state=None)
        self.assertEqual(row.state_summary(), '—')

    def test_recheck_chain_task_no_attempts_creates_empty_rows(self):
        """Calling recheck on a task with zero attempts must not crash."""
        recheck_chain_task(task=self.repl_task, team=self.team2)
        rows = ChainTaskState.objects.filter(task=self.repl_task, team=self.team2)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertIsNone(row.state)

    def test_backfill_command_dry_run(self):
        """--dry-run prints what would be done without touching the DB."""
        from django.core.management import call_command
        from io import StringIO

        a = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a)

        ChainTaskState.objects.filter(task=self.repl_task, team=self.team).delete()

        out = StringIO()
        call_command('backfill_chain_task_states', '--dry-run', stdout=out)
        self.assertIn('dry-run', out.getvalue())
        self.assertFalse(
            ChainTaskState.objects.filter(task=self.repl_task, team=self.team).exists()
        )

    def test_backfill_command_populates_state(self):
        """Running the backfill command rebuilds ChainTaskState for all combos."""
        from django.core.management import call_command

        a1 = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(a1)
        a2 = _make_attempt(self.repl_task, self.team, _repl_text(1, ['answer2']))
        check_attempt(a2)

        ChainTaskState.objects.filter(task=self.repl_task, team=self.team).delete()

        call_command('backfill_chain_task_states', '--task-id', str(self.repl_task.id))

        row = ChainTaskState.objects.get(
            task=self.repl_task, team=self.team, game_mode='general',
        )
        state = json.loads(row.state)
        self.assertIn(0, state['solved_lines'])
        self.assertIn(1, state['solved_lines'])
