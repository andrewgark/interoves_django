from django.test import SimpleTestCase
from django.urls import resolve

from games.views.new_ui import new_replay_start


class ReplayUrlTests(SimpleTestCase):
    def test_alphabetty_replay_url_resolves_to_post_view(self):
        match = resolve('/games/alphabetty/49/replay/')

        self.assertIs(match.func, new_replay_start)
        self.assertEqual(match.kwargs, {
            'game_id': 'alphabetty',
            'task_group_number': '49',
        })
