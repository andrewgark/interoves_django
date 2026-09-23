import json

from django.contrib.auth.models import User
from django.test import TestCase

from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    PlayerCompletedGame,
    PlayerAnalyticsState,
    Project,
    Task,
    TaskGroup,
)
from games.recheck import recheck_word_salad_actor
from games.word_salad_offer import reset_all_salad_progress, reset_salad_progress


class TargetedCompletionReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(pk='sections')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            from games.models import HTMLPage
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.game, _ = Game.objects.get_or_create(
            id='salad',
            defaults={
                'name': 'Targeted completion game',
                'author': 'test',
                'project': cls.project,
                'is_ready': True,
            },
        )
        cls.checker, _ = CheckerType.objects.get_or_create(pk='word_salad')
        cls.user = User.objects.create_user(username='targeted-completion-user')

    def _fixture(self):
        group = TaskGroup.objects.create(label='targeted-completion-group')
        GameTaskGroup.objects.create(game=self.game, task_group=group, number='1')
        task = Task.objects.create(
            task_group=group,
            number='1',
            task_type='word_salad',
            checker=self.checker,
            checker_data=json.dumps({'grid': list('ABCDEFGHIJKLMNOP'), 'words': ['ABCD']}),
        )
        ChainTaskState.objects.create(
            task=task,
            game=self.game,
            user=self.user,
            game_mode='general',
            state=json.dumps({'solved_indices': [0], 'active': []}),
        )
        return group, task

    def _pcg(self, group):
        return PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=group,
            game_kind='word_salad',
            game_instance_id='{}:{}'.format(self.game.pk, group.pk),
            is_backfilled=True,
        )

    def test_task_is_removed_changes_reconcile_only_attached_actor(self):
        group, task = self._fixture()
        self._pcg(group)

        with self.captureOnCommitCallbacks(execute=True):
            task.is_removed = True
            task.save(update_fields=['is_removed'])
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

        with self.captureOnCommitCallbacks(execute=True):
            task.is_removed = False
            task.save(update_fields=['is_removed'])
        self.assertTrue(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_mapping_delete_removes_only_its_canonical_completion(self):
        group, _task = self._fixture()
        self._pcg(group)
        link = GameTaskGroup.objects.get(game=self.game, task_group=group)

        with self.captureOnCommitCallbacks(execute=True):
            link.delete()
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_mapping_create_reconciles_existing_group_history(self):
        group, _task = self._fixture()
        GameTaskGroup.objects.filter(game=self.game, task_group=group).delete()
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

        with self.captureOnCommitCallbacks(execute=True):
            GameTaskGroup.objects.create(game=self.game, task_group=group, number='1')
        self.assertTrue(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_task_delete_reconciles_remaining_mapping_history(self):
        group, task = self._fixture()
        self._pcg(group)

        with self.captureOnCommitCallbacks(execute=True):
            task.delete()

        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_task_addition_and_removal_reconcile_group_membership(self):
        group, task = self._fixture()
        self._pcg(group)
        second = Task(
            task_group=group,
            number='2',
            task_type='word_salad',
            checker=self.checker,
            checker_data=json.dumps({'grid': list('ABCDEFGHIJKLMNOP'), 'words': ['EFGH']}),
        )

        with self.captureOnCommitCallbacks(execute=True):
            second.save()
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

        with self.captureOnCommitCallbacks(execute=True):
            second.delete()
        self.assertTrue(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_word_salad_recheck_removes_invalid_pcg_without_goals(self):
        group, task = self._fixture()
        self._pcg(group)
        task.checker_data = json.dumps({'grid': list('ZYXWVUTSRQPONMLK'), 'words': ['ABCD']})
        task.save(update_fields=['checker_data'])

        recheck_word_salad_actor(
            task,
            game=self.game,
            user=self.user,
            notify=False,
        )
        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())
        self.assertFalse(PlayerAnalyticsState.objects.filter(user=self.user).exists())

    def test_salad_reset_removes_pcg_for_the_reset_actor(self):
        group, task = self._fixture()
        self._pcg(group)

        reset_salad_progress(task=task, game_id=self.game.pk, user=self.user)

        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())

    def test_salad_bulk_reset_reconciles_only_attached_actors(self):
        group, task = self._fixture()
        self._pcg(group)

        reset_all_salad_progress(task=task, game_id=self.game.pk)

        self.assertFalse(PlayerCompletedGame.objects.filter(user=self.user).exists())
        self.assertFalse(PlayerAnalyticsState.objects.filter(user=self.user).exists())
