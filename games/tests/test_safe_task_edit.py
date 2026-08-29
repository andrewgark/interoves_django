import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from django.db import close_old_connections, OperationalError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

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
    Team,
)
from games.recheck import recheck_chain_task
from games.scoring import Actor, bulk_actor_solved_task_ids
from games.views.attempt_views import check_attempt


SALAD_PATH = [0, 1, 2, 3, 7, 6, 5, 4, 8, 9, 10, 11, 15, 14, 13, 12]
SHORT_PATH = SALAD_PATH[:4]
HYPOMANIA_PATH = SALAD_PATH[:9]


def _salad_grid():
    cells = ['X'] * 16
    for index, letter in zip(SALAD_PATH, 'ГИПОМАНИЯАБВГДЕЖ'):
        cells[index] = letter
    return cells


def _task_data(words, grid=None):
    return json.dumps({
        'grid': grid or _salad_grid(),
        'words': words,
    }, ensure_ascii=False)


class _SafeEditFixture:
    @classmethod
    def make_fixtures(cls, suffix):
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        checker, _ = CheckerType.objects.get_or_create(pk='word_salad')
        cls.game = Game.objects.create(
            id='safe-edit-{}'.format(suffix),
            name='Safe edit',
            author='tests',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )
        cls.group = TaskGroup.objects.create(
            label='safe-edit-{}'.format(suffix), points=1, max_attempts=None,
        )
        GameTaskGroup.objects.create(
            game=cls.game, task_group=cls.group, number='1', name='Салатик',
        )
        cls.task = Task.objects.create(
            task_group=cls.group,
            number='1',
            task_type='word_salad',
            checker=checker,
            checker_data=_task_data(['ГИПО']),
            text='Тема: состояния',
            points=1,
            max_attempts=None,
        )
        cls.team = Team.objects.create(
            name='safe-edit-team-{}'.format(suffix), visible_name='Safe',
            project_id='sections',
        )

    def submit(self, path, *, team=None):
        attempt = Attempt(
            task=self.task,
            team=team or self.team,
            game=self.game,
            text=json.dumps({'action': 'solve', 'path': path}),
            time=timezone.now(),
        )
        check_attempt(attempt)
        return attempt

    def edit_words(self, words):
        self.task.checker_data = _task_data(words)
        self.task.save(update_fields=['checker_data'])


class SafePublishedTaskEditTests(_SafeEditFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.make_fixtures('main')

    def test_unsolved_without_affected_submission_stays_unsolved(self):
        unrelated = self.submit(SALAD_PATH[9:12])
        self.assertEqual(unrelated.status, 'Wrong')
        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])

        unrelated.refresh_from_db()
        state = json.loads(ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state)
        self.assertEqual(state['solved_indices'], [])
        self.assertEqual(unrelated.current_status, 'Wrong')
        self.assertEqual(unrelated.status, 'Wrong')
        self.assertFalse(
            Attempt.manager.get_attempts_info(self.team, self.task, game=self.game).is_solved()
        )

    def test_old_wrong_hypomania_becomes_correct(self):
        old = self.submit(HYPOMANIA_PATH)
        self.assertEqual(old.status, 'Wrong')

        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])

        old.refresh_from_db()
        state = json.loads(ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state)
        self.assertEqual(state['solved_indices'], [1])
        self.assertEqual(old.current_status, 'Partial')
        self.assertEqual(old.status, 'Partial')
        self.assertEqual(old.current_points, Decimal('1'))

    def test_partial_progress_is_improved_by_recheck(self):
        self.task.checker_data = _task_data(['ГИПО', 'АБВ'])
        self.task.save(update_fields=['checker_data'])
        self.submit(SHORT_PATH)
        affected = self.submit(HYPOMANIA_PATH)
        before = json.loads(ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state)
        self.assertEqual(len(before['solved_indices']), 1)

        self.edit_words(['ГИПО', 'АБВ', 'ГИПОМАНИЯ'])

        affected.refresh_from_db()
        after = json.loads(ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state)
        self.assertEqual(set(after['solved_indices']), {0, 2})
        self.assertEqual(affected.current_points, Decimal('2'))

    def test_solved_without_new_word_remains_solved_at_eight_of_nine_shape(self):
        solved = self.submit(SHORT_PATH)
        row = ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        )
        completed_at = row.completed_at
        self.assertIsNotNone(completed_at)

        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])

        solved.refresh_from_db()
        row.refresh_from_db()
        info = Attempt.manager.get_attempts_info(self.team, self.task, game=self.game)
        self.assertTrue(info.is_solved())
        self.assertEqual(info.get_result_points(), Decimal('1'))
        self.assertEqual(self.task.get_results_max_points(), 2)
        self.assertEqual(row.completed_at, completed_at)
        self.assertEqual(solved.current_status, 'Partial')
        self.assertEqual(solved.status, 'Ok')
        self.assertIn(
            self.task.pk,
            bulk_actor_solved_task_ids(
                tasks=[self.task],
                actor=Actor(team_id=self.team.pk),
                game=self.game,
            ),
        )

    def test_solved_and_old_wrong_new_word_becomes_currently_complete(self):
        affected = self.submit(HYPOMANIA_PATH)
        solved = self.submit(SHORT_PATH)
        completed_at = ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game,
        ).completed_at

        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])

        affected.refresh_from_db()
        solved.refresh_from_db()
        row = ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        )
        state = json.loads(row.state)
        self.assertEqual(set(state['solved_indices']), {0, 1})
        self.assertEqual(affected.current_status, 'Partial')
        self.assertEqual(solved.current_status, 'Ok')
        self.assertEqual(solved.points, Decimal('2'))
        self.assertEqual(row.completed_at, completed_at)

    def test_current_max_points_grows_without_revoking_completion(self):
        self.submit(SHORT_PATH)
        self.assertEqual(self.task.get_results_max_points(), 1)
        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])
        self.assertEqual(self.task.get_results_max_points(), 2)
        self.assertTrue(
            Attempt.manager.get_attempts_info(self.team, self.task, game=self.game).is_solved()
        )

    def test_recheck_can_reduce_current_result_but_not_awarded_result(self):
        solved = self.submit(SHORT_PATH)
        changed_grid = list(_salad_grid())
        changed_grid[0] = 'Я'
        self.task.checker_data = _task_data(['ГИПО'], grid=changed_grid)
        self.task.save(update_fields=['checker_data'])

        solved.refresh_from_db()
        info = Attempt.manager.get_attempts_info(self.team, self.task, game=self.game)
        self.assertEqual(solved.current_status, 'Wrong')
        self.assertEqual(solved.current_points, Decimal('0'))
        self.assertEqual(solved.status, 'Ok')
        self.assertEqual(info.get_result_points(), Decimal('1'))
        self.assertTrue(info.is_solved())

    def test_removing_answer_changes_current_submission_only(self):
        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])
        removed = self.submit(HYPOMANIA_PATH)
        completed = self.submit(SHORT_PATH)
        self.edit_words(['ГИПО'])

        removed.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(removed.current_status, 'Wrong')
        self.assertEqual(removed.status, 'Partial')
        self.assertEqual(completed.status, 'Ok')
        self.assertTrue(
            Attempt.manager.get_attempts_info(self.team, self.task, game=self.game).is_solved()
        )

    def test_recheck_is_idempotent(self):
        self.submit(HYPOMANIA_PATH)
        self.submit(SHORT_PATH)
        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])
        before_attempts = list(Attempt.manager.filter(task=self.task).values(
            'id', 'status', 'points', 'current_status', 'current_points',
            'state', 'recheck_points_floor',
        ))
        before_state = ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state

        recheck_chain_task(self.task, team=self.team, game=self.game, notify=False)

        self.assertEqual(before_attempts, list(Attempt.manager.filter(task=self.task).values(
            'id', 'status', 'points', 'current_status', 'current_points',
            'state', 'recheck_points_floor',
        )))
        self.assertEqual(before_state, ChainTaskState.objects.get(
            task=self.task, team=self.team, game=self.game, game_mode='general',
        ).state)

    def test_cosmetic_text_change_does_not_rotate_revision_or_recheck(self):
        self.submit(SHORT_PATH)
        revision = self.task.attempt_revision
        self.task.text = 'Тема: новый красивый текст'
        with patch('games.recheck.recheck_task_after_edit') as replay:
            self.task.save(update_fields=['text'])
        self.assertEqual(self.task.attempt_revision, revision)
        replay.assert_not_called()

    def test_non_chain_task_uses_same_monotonic_recheck(self):
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        ordinary = Task.objects.create(
            task_group=self.group,
            number='2',
            task_type='default',
            checker=checker,
            checker_data='СТАРЫЙ',
            answer='СТАРЫЙ',
            points=3,
        )
        attempt = Attempt(
            task=ordinary,
            team=self.team,
            game=self.game,
            text='СТАРЫЙ',
            time=timezone.now(),
        )
        check_attempt(attempt)
        self.assertEqual(attempt.status, 'Ok')
        self.assertEqual(attempt.points, Decimal('3'))

        ordinary.checker_data = 'НОВЫЙ'
        ordinary.answer = 'НОВЫЙ'
        ordinary.save(update_fields=['checker_data', 'answer'])

        attempt.refresh_from_db()
        self.assertEqual(attempt.current_status, 'Wrong')
        self.assertEqual(attempt.current_points, Decimal('0'))
        self.assertEqual(attempt.status, 'Ok')
        self.assertEqual(attempt.points, Decimal('3'))

    def test_inherited_task_group_points_trigger_safe_recheck(self):
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        ordinary = Task.objects.create(
            task_group=self.group,
            number='3',
            task_type='default',
            checker=checker,
            checker_data='ДА',
            answer='ДА',
            points=None,
        )
        attempt = Attempt(
            task=ordinary,
            team=self.team,
            game=self.game,
            text='ДА',
            time=timezone.now(),
        )
        check_attempt(attempt)
        self.assertEqual(attempt.points, Decimal('1'))

        self.group.points = Decimal('0.5')
        self.group.save(update_fields=['points'])

        attempt.refresh_from_db()
        ordinary.refresh_from_db()
        self.assertEqual(attempt.current_points, Decimal('0.5'))
        self.assertEqual(attempt.points, Decimal('1'))
        self.assertEqual(attempt.status, 'Ok')

    def test_legacy_points_equal_old_max_is_materialised_as_completion(self):
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        ordinary = Task.objects.create(
            task_group=self.group,
            number='4',
            task_type='default',
            checker=checker,
            checker_data='ДА',
            answer='ДА',
            points=3,
        )
        attempt = Attempt.manager.create(
            task=ordinary,
            team=self.team,
            game=self.game,
            text='НЕТ',
            status='Partial',
            points=3,
            task_revision=ordinary.attempt_revision,
        )

        ordinary.points = 4
        ordinary.save(update_fields=['points'])

        attempt.refresh_from_db()
        self.assertEqual(attempt.current_status, 'Wrong')
        self.assertEqual(attempt.current_points, Decimal('0'))
        self.assertEqual(attempt.status, 'Ok')
        self.assertEqual(attempt.points, Decimal('3'))
        self.assertIn(
            ordinary.pk,
            bulk_actor_solved_task_ids(
                tasks=[ordinary], actor=Actor(team_id=self.team.pk), game=self.game,
            ),
        )


class SafeTaskEditConcurrencyTests(_SafeEditFixture, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.make_fixtures('parallel')

    def test_two_parallel_rechecks_do_not_double_award(self):
        self.submit(HYPOMANIA_PATH)
        self.submit(SHORT_PATH)
        self.edit_words(['ГИПО', 'ГИПОМАНИЯ'])

        def run():
            close_old_connections()
            try:
                task = Task.objects.get(pk=self.task.pk)
                team = Team.objects.get(pk=self.team.pk)
                game = Game.objects.get(pk=self.game.pk)
                recheck_chain_task(task, team=team, game=game, notify=False)
                return True
            except OperationalError:
                # SQLite has table-level locks rather than SELECT FOR UPDATE.
                # A rejected contender is safe to retry because replay is
                # idempotent; MySQL/PostgreSQL serialize both calls directly.
                return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = list(pool.map(lambda _index: run(), range(2)))
        for succeeded in completed:
            if not succeeded:
                self.assertTrue(run())

        attempts = list(Attempt.manager.filter(task=self.task, team=self.team).order_by('time'))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(max(attempt.points for attempt in attempts), Decimal('2'))
        self.assertEqual(
            Attempt.manager.get_attempts_info(self.team, self.task, game=self.game).get_result_points(),
            Decimal('2'),
        )
