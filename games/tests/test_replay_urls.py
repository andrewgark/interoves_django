from django.test import SimpleTestCase
from django.urls import resolve

from games.views.new_ui import new_replay_start


class ReplayUrlTests(SimpleTestCase):
    def assert_replay_view(self, path, expected):
        match = resolve(path)
        self.assertIs(match.func, new_replay_start)
        self.assertEqual(match.kwargs, expected)

    def test_alphabetty_replay_url_resolves_to_post_view(self):
        self.assert_replay_view('/games/alphabetty/49/replay/', {
            'game_id': 'alphabetty',
            'task_group_number': '49',
        })

    def test_root_section_replay_urls_resolve_to_post_view(self):
        for game_id in ('ladder', 'alphabetty', 'salad'):
            with self.subTest(game_id=game_id):
                self.assert_replay_view('/{}/73/replay/'.format(game_id), {
                    'game_id': game_id,
                    'task_group_number': '73',
                })

    def test_project_replay_url_resolves_to_post_view(self):
        self.assert_replay_view('/glowbyte/games/quiz/73/replay/', {
            'project_id': 'glowbyte',
            'game_id': 'quiz',
            'task_group_number': '73',
        })
