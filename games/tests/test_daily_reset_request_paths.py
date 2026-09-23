"""Phase 3 guardrails: user-facing requests must not run daily cleanup."""

import inspect
import unittest

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from games.models import Game, GameTaskGroup, Task, TaskGroup
from games.views.game_context import unpublished_scheduled_task_response
from games.views import daily_timing_views, new_ui


class DailyResetRequestPathTests(unittest.TestCase):
    def test_timing_target_loader_has_no_destructive_reset(self):
        source = inspect.getsource(daily_timing_views._load_daily_target)
        self.assertNotIn('reset_current_daily_release_progress', source)

    def test_task_group_page_has_no_destructive_reset(self):
        source = inspect.getsource(new_ui.project_task_group_page)
        self.assertNotIn('reset_current_daily_release_progress', source)

    def test_new_task_group_page_has_no_destructive_reset(self):
        source = inspect.getsource(new_ui.new_task_group_page)
        self.assertNotIn('reset_current_daily_release_progress', source)


class DailyReleaseWriteGuardTests(TestCase):
    def setUp(self):
        self.game = Game.objects.get(pk='ladder')
        self.game.tags = {'ladder_publish_start': '2099-01-01T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])
        group = TaskGroup.objects.create(label='unpublished-release')
        GameTaskGroup.objects.create(game=self.game, task_group=group, number='2', name='Future')
        self.task = Task.objects.create(task_group=group, number='1', task_type='raddle')
        self.request = RequestFactory().post('/')
        self.request.user = AnonymousUser()

    def test_authoritative_write_is_blocked_before_publication(self):
        result = unpublished_scheduled_task_response(self.request, self.game, self.task)
        self.assertEqual(result['status'], 'not_published')

    def test_authoritative_write_is_allowed_after_publication(self):
        self.game.tags = {'ladder_publish_start': '2020-01-01T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])
        self.assertIsNone(
            unpublished_scheduled_task_response(self.request, self.game, self.task)
        )

    def test_authoritative_entrypoints_use_the_publication_guard(self):
        attempt_source = inspect.getsource(__import__('games.views.attempt_views', fromlist=['process_send_attempt']).process_send_attempt)
        raddle_source = inspect.getsource(__import__('games.views.raddle_views', fromlist=['process_send_raddle_assist']).process_send_raddle_assist)
        self.assertIn('unpublished_scheduled_task_response', attempt_source)
        self.assertIn('unpublished_scheduled_task_response', raddle_source)
