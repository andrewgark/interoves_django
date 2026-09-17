from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from games.analytics import game_instance_id_for_task_group, is_task_group_complete
from games.analytics_identity import stamp_anon_identity
from games.models import (
    Attempt,
    CheckerType,
    ChainTaskState,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    Hint,
    HintAttempt,
    HTMLPage,
    PlayerCompletedGame,
    Project,
    ReplaySlot,
    Task,
    TaskGroup,
    Team,
)
from games.replay import StaleReplayError, replay_for_request, start_or_reset_replay
from games.account_merge import _merge_replay_slots
from games.views.attempt_views import process_send_attempt
from games.results_sql_aggregate import get_sql_aggregated_game_actor_rows


class ReplaySlotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        cls.game = Game.objects.create(id='replay_test', name='Replay', author='t', is_ready=True)
        cls.group = TaskGroup.objects.create(label='replay-test-group')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number=1, name='1')
        cls.task = Task.objects.create(
            task_group=cls.group, number='1', checker=CheckerType.objects.get(pk='equals'),
            answer='ok', points=10,
        )
        cls.user = User.objects.create_user(username='replay-user', password='x')

    def _request(self):
        request = RequestFactory().post('/replay/')
        SessionMiddleware(lambda _request: None).process_request(request)
        request.session.save()
        return request

    def _official(self):
        return PlayerCompletedGame.objects.create(
            user=self.user, game=self.game, task_group=self.group,
            game_kind='replay_test',
            game_instance_id=game_instance_id_for_task_group(self.game, self.group),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

    def test_reset_rotates_one_slot_and_deletes_previous_namespace(self):
        self._official()
        request = self._request()
        first = start_or_reset_replay(
            request=request, game=self.game, task_group=self.group, user=self.user,
        )
        Attempt.manager.create(user=self.user, game=self.game, task=self.task,
                               replay_slot=first, text='old', status='Wrong')
        second = start_or_reset_replay(
            request=request, game=self.game, task_group=self.group, user=self.user,
        )
        self.assertEqual(ReplaySlot.objects.filter(user=self.user).count(), 1)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertFalse(Attempt.manager.get_queryset().filter(replay_slot=second).exists())

    def test_stale_run_is_rejected(self):
        self._official()
        request = self._request()
        slot = start_or_reset_replay(
            request=request, game=self.game, task_group=self.group, user=self.user,
        )
        request.interoves_replay_slot_id = slot.pk
        request.interoves_replay_run_id = str(slot.run_id)
        start_or_reset_replay(
            request=self._request(), game=self.game, task_group=self.group, user=self.user,
        )
        with self.assertRaises(StaleReplayError):
            replay_for_request(
                request=request, game=self.game, task_group=self.group, user=self.user,
            )

    def test_normal_submission_after_official_completion_is_replay_required(self):
        anon_key = 'replay-direct-anon'
        PlayerCompletedGame.objects.create(
            anon_key=anon_key,
            game=self.game,
            task_group=self.group,
            game_kind='replay_test',
            game_instance_id=game_instance_id_for_task_group(self.game, self.group),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        request = RequestFactory().post('/send/', {'text': 'ok'})
        request.user = AnonymousUser()
        stamp_anon_identity(request, anon_key)
        with patch('games.views.attempt_views.game_from_request_for_task', return_value=self.game), \
             patch('games.views.attempt_views._get_play_mode', return_value='personal'):
            result = process_send_attempt(request, self.task.pk)
        self.assertEqual(result['error'], 'replay_required')
        self.assertFalse(Attempt.manager.filter(anon_key=anon_key).exists())

    def test_task_group_completion_requires_every_task(self):
        second = Task.objects.create(
            task_group=self.group, number='2', checker=CheckerType.objects.get(pk='equals'),
            answer='ok', points=10,
        )
        Attempt.manager.create(
            user=self.user, game=self.game, task=self.task,
            text='ok', status='Ok', points=10,
        )
        self.assertFalse(is_task_group_complete(
            task_group=self.group, game=self.game, user=self.user,
        ))
        Attempt.manager.create(
            user=self.user, game=self.game, task=second,
            text='ok', status='Ok', points=10,
        )
        self.assertTrue(is_task_group_complete(
            task_group=self.group, game=self.game, user=self.user,
        ))

    def test_sql_results_ignore_replay_attempts_and_hints(self):
        slot = ReplaySlot.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.group,
            actor_key='user:{}'.format(self.user.pk),
        )
        Attempt.manager.create(user=self.user, game=self.game, task=self.task,
                               text='official', status='Ok', points=10)
        Attempt.manager.create(user=self.user, game=self.game, task=self.task,
                               replay_slot=slot, text='replay', status='Ok', points=99)
        hint = Hint.objects.create(task=self.task, number='1', points_penalty=5)
        HintAttempt.objects.create(user=self.user, hint=hint, replay_slot=slot, is_real_request=True)
        rows = get_sql_aggregated_game_actor_rows([self.task.pk], game=self.game)
        self.assertEqual(rows[self.task.pk][0][1].get_n_attempts(), 1)
        self.assertEqual(rows[self.task.pk][0][1].get_result_points(), 10)

    def test_account_merge_target_slot_wins_and_source_data_is_cascaded(self):
        source = User.objects.create_user(username='replay-source', password='x')
        target = User.objects.create_user(username='replay-target', password='x')
        target_slot = ReplaySlot.objects.create(
            user=target, game=self.game, task_group=self.group,
            actor_key='user:{}'.format(target.pk),
        )
        source_slot = ReplaySlot.objects.create(
            user=source, game=self.game, task_group=self.group,
            actor_key='user:{}'.format(source.pk),
        )
        hint = Hint.objects.create(task=self.task, number='merge', points_penalty=1)
        Attempt.manager.create(
            user=source, game=self.game, task=self.task, replay_slot=source_slot,
            text='source', status='Wrong', points=0,
        )
        HintAttempt.objects.create(
            user=source, hint=hint, replay_slot=source_slot, is_real_request=True,
        )
        ChainTaskState.objects.create(
            user=source, task=self.task, game=self.game, replay_slot=source_slot,
            game_mode='general', state='source',
        )
        DailySolveTiming.objects.create(
            user=source, game=self.game, task_group=self.group, replay_slot=source_slot,
        )

        _merge_replay_slots(target, source)

        target_slot.refresh_from_db()
        self.assertEqual(ReplaySlot.objects.filter(user=target).count(), 1)
        self.assertFalse(ReplaySlot.objects.filter(pk=source_slot.pk).exists())
        self.assertEqual(Attempt.manager.filter(replay_slot=source_slot).count(), 0)
        self.assertEqual(HintAttempt.objects.filter(replay_slot=source_slot).count(), 0)
        self.assertEqual(ChainTaskState.objects.filter(replay_slot=source_slot).count(), 0)
        self.assertEqual(DailySolveTiming.objects.filter(replay_slot=source_slot).count(), 0)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 0)
