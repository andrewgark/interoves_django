from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.test import RequestFactory, TestCase, override_settings

from games.gameplay_context import (
    actor_descriptor,
    issue_gameplay_context,
    validate_gameplay_context,
)
from games.admin import AttemptAdmin
from games.models import Attempt


class GameplayContextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.game = SimpleNamespace(pk='game-1')
        self.task = SimpleNamespace(pk=6159)
        self.team = SimpleNamespace(pk=7)
        self.user = get_user_model().objects.create_user(username='context-user')

    def _request(self, token=None):
        data = {}
        if token is not None:
            data['gameplay_context'] = token
        request = self.factory.post('/send_attempt/6159/', data)
        request.user = self.user
        return request

    def test_actor_descriptor_prefers_team_then_user_then_anon(self):
        self.assertEqual(actor_descriptor(team=self.team, user=self.user), ('team', '7'))
        self.assertEqual(actor_descriptor(user=self.user), ('user', str(self.user.pk)))
        kind, identifier = actor_descriptor(anon_key='anon-x')
        self.assertEqual(kind, 'anon')
        self.assertEqual(len(identifier), 64)

    def test_same_actor_context_survives_session_rotation(self):
        token = issue_gameplay_context(task=self.task, game=self.game, user=self.user)
        request = self._request(token)
        self.assertIsNone(validate_gameplay_context(
            request, task=self.task, game=self.game, user=self.user,
        ))
        self.assertEqual(request.interoves_gameplay_context_result, 'valid')

    def test_user_switch_is_rejected(self):
        token = issue_gameplay_context(task=self.task, game=self.game, user=self.user)
        other = get_user_model().objects.create_user(username='other-context-user')
        request = self._request(token)
        error = validate_gameplay_context(
            request, task=self.task, game=self.game, user=other,
        )
        self.assertEqual(error['error'], 'gameplay_actor_context_mismatch')

    def test_wrong_task_is_rejected(self):
        token = issue_gameplay_context(task=self.task, game=self.game, user=self.user)
        request = self._request(token)
        error = validate_gameplay_context(
            request, task=SimpleNamespace(pk=6741), game=self.game, user=self.user,
        )
        self.assertEqual(error['error'], 'gameplay_context_task_mismatch')

    def test_tampered_token_is_rejected(self):
        token = issue_gameplay_context(task=self.task, game=self.game, user=self.user)
        request = self._request(token + 'tampered')
        error = validate_gameplay_context(
            request, task=self.task, game=self.game, user=self.user,
        )
        self.assertEqual(error['error'], 'invalid_gameplay_context')

    @override_settings(GAMEPLAY_CONTEXT_REQUIRE_TOKEN=True)
    def test_missing_token_is_rejected_in_phase_b(self):
        request = self._request()
        error = validate_gameplay_context(
            request, task=self.task, game=self.game, user=self.user,
        )
        self.assertEqual(error['error'], 'gameplay_context_required')

    def test_task_group_context_binds_group_not_individual_task(self):
        group = SimpleNamespace(pk=55)
        token = issue_gameplay_context(
            task_group=group, game=self.game, user=self.user,
        )
        request = self._request(token)
        self.assertIsNone(validate_gameplay_context(
            request, task_group=group, game=self.game, user=self.user,
        ))

    def test_attempt_admin_locks_ownership_on_existing_rows(self):
        model_admin = AttemptAdmin(Attempt, admin.site)
        self.assertEqual(
            set(model_admin.get_readonly_fields(self._request(), object())),
            {'user', 'anon_key', 'team'},
        )
