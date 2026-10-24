from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from games.exception import NoGameAccessException
from games.identity.actor import Actor, ActorRefusal, resolve_gameplay_actor


def _game(*, allowed=True):
    return SimpleNamespace(
        has_access=lambda *args, **kwargs: allowed,
        __str__=lambda self: 'ladder',
    )


class ResolveGameplayActorTests(SimpleTestCase):
    def test_team_mode_returns_the_primary_team(self):
        team = SimpleNamespace(pk=7)
        user = SimpleNamespace(
            is_authenticated=True,
            profile=SimpleNamespace(team_on=team),
        )
        request = SimpleNamespace(user=user)
        with patch('games.identity.actor.has_team', return_value=True):
            actor = resolve_gameplay_actor(request, _game(), 'team')

        self.assertIsInstance(actor, Actor)
        self.assertEqual(actor.kind, 'team')
        self.assertIs(actor.team, team)
        self.assertIsNone(actor.user)
        self.assertIsNone(actor.anon_key)

    def test_team_mode_without_a_team_refuses(self):
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True))
        with patch('games.identity.actor.has_team', return_value=False):
            refused = resolve_gameplay_actor(request, _game(), 'team')

        self.assertEqual(refused, ActorRefusal('no_team'))

    def test_personal_mode_uses_the_authenticated_user(self):
        user = SimpleNamespace(is_authenticated=True)
        request = SimpleNamespace(user=user)
        with patch('games.identity.actor.has_profile', return_value=True):
            actor = resolve_gameplay_actor(request, _game(), 'personal')

        self.assertEqual(actor.kind, 'user')
        self.assertIs(actor.user, user)
        self.assertEqual(actor.play_mode, 'personal')

    def test_authenticated_user_without_profile_refuses(self):
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True))
        with patch('games.identity.actor.has_profile', return_value=False):
            refused = resolve_gameplay_actor(request, _game(), 'personal')

        self.assertEqual(refused, ActorRefusal('no_profile'))

    def test_anonymous_player_uses_the_server_anon_key(self):
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
        with patch('games.identity.actor.gameplay_anon_key', return_value='anon-1'):
            actor = resolve_gameplay_actor(request, _game(), 'personal')

        self.assertEqual(actor.kind, 'anon')
        self.assertEqual(actor.anon_key, 'anon-1')

    def test_missing_anon_key_refuses(self):
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
        with patch('games.identity.actor.gameplay_anon_key', return_value=''):
            refused = resolve_gameplay_actor(request, _game(), 'personal')

        self.assertEqual(refused, ActorRefusal('no_anon'))

    def test_denied_access_raises_the_existing_exception(self):
        user = SimpleNamespace(is_authenticated=True)
        request = SimpleNamespace(user=user)
        with patch('games.identity.actor.has_profile', return_value=True):
            with self.assertRaises(NoGameAccessException):
                resolve_gameplay_actor(request, _game(allowed=False), 'personal')

    def test_duplicate_sync_can_skip_the_access_check(self):
        user = SimpleNamespace(is_authenticated=True)
        request = SimpleNamespace(user=user)
        game = _game(allowed=False)
        with patch('games.identity.actor.has_profile', return_value=True):
            actor = resolve_gameplay_actor(
                request, game, 'personal', check_access=False,
            )

        self.assertEqual(actor.kind, 'user')

    def test_actor_rejects_a_mixed_identity(self):
        with self.assertRaises(ValueError):
            Actor(play_mode='personal', user=object(), anon_key='anon')
