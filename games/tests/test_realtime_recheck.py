from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.ops_actions import confirm_attempt_prestatus, set_attempt_ok
from games.recheck import recheck


class RealtimeAttemptReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        now = timezone.now()
        cls.game = Game.objects.create(
            id='realtime_review_game',
            name='Realtime review',
            author='test',
            is_ready=True,
            is_playable=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        cls.group = TaskGroup.objects.create(label='Realtime review')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number=1)
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                checker=checker,
                checker_data='right',
                points=1,
            )
        cls.team = Team.objects.create(name='realtime_review_team')

    def make_attempt(self, **overrides):
        values = {
            'task': self.task,
            'game': self.game,
            'team': self.team,
            'text': 'right',
            'status': 'Pending',
            'points': 0,
        }
        values.update(overrides)
        return Attempt.manager.create(**values)

    def test_set_ok_publishes_actor_task_change(self):
        attempt = self.make_attempt()
        with patch('games.ops_actions.track_attempt_change') as notify:
            set_attempt_ok(attempt)
        notify.assert_called_once_with(attempt, reason='attempt.set_ok')

    def test_confirm_prestatus_publishes_actor_task_change(self):
        attempt = self.make_attempt(possible_status='Wrong')
        with patch('games.ops_actions.track_attempt_change') as notify:
            confirm_attempt_prestatus(attempt)
        notify.assert_called_once_with(attempt, reason='attempt.prestatus_confirmed')

    def test_recheck_publishes_actor_task_change(self):
        attempt = self.make_attempt()
        with patch('games.recheck.track_attempt_change') as notify:
            rechecked = recheck(None, attempt.pk)
        self.assertEqual(rechecked.pk, attempt.pk)
        notify.assert_called_once_with(rechecked, reason='attempt.rechecked')
