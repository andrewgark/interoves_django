import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.models import Attempt
from games.puzzles import get_puzzle
from games.puzzles.distribute_to_teams import DistributeToTeamsPuzzle


class DistributePrettyTextTests(SimpleTestCase):
    def test_team_line_is_appended(self):
        attempt = SimpleNamespace(
            text='ответ',
            game_id=1,
            game=object(),
            task=SimpleNamespace(
                task_type='distribute_to_teams',
                text=json.dumps({'list': ['первая', 'вторая']}),
            ),
            team=SimpleNamespace(get_team_reg_number=lambda game: 1),
        )
        self.assertEqual(Attempt.get_pretty_text(attempt), 'ответ (вторая)')

    def test_bad_json_is_an_error_suffix(self):
        attempt = SimpleNamespace(
            text='ответ',
            game_id=1,
            game=object(),
            task=SimpleNamespace(task_type='distribute_to_teams', text='not-json'),
            team=SimpleNamespace(get_team_reg_number=lambda game: 0),
        )
        self.assertTrue(Attempt.get_pretty_text(attempt).startswith('ответ (ERROR:'))

    def test_pretty_text_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('distribute_to_teams'))
        self.assertEqual(DistributeToTeamsPuzzle.task_type, 'distribute_to_teams')
