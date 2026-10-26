from types import SimpleNamespace

from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.markers.with_tag import WithTagPuzzle


class WithTagDependentActorTests(SimpleTestCase):
    def test_missing_task_tag_returns_none(self):
        task = SimpleNamespace(tags={'team': 'Red'})
        self.assertIsNone(WithTagPuzzle.dependent_actor(task))

    def test_missing_team_tag_returns_none(self):
        task = SimpleNamespace(tags={'task': '1'})
        self.assertIsNone(WithTagPuzzle.dependent_actor(task))

    def test_lookup_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('with_tag'))
