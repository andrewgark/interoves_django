"""URL routing smoke for scripts/smoke_prod_pages.list (post-deploy checklist)."""

from pathlib import Path

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

_LIST = Path(__file__).resolve().parents[2] / 'scripts' / 'smoke_prod_pages.list'


def _listed_paths():
    paths = []
    for raw in _LIST.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if line.startswith('/'):
            paths.append(line)
    return paths


class SmokeProdPagesListTests(SimpleTestCase):
    def test_list_file_exists_and_nonempty(self):
        self.assertTrue(_LIST.is_file(), msg='missing {}'.format(_LIST))
        self.assertGreaterEqual(len(_listed_paths()), 5)

    def test_listed_paths_resolve(self):
        """Catch catch-all collisions (e.g. /ladder/results/ → task_group 'results')."""
        for path in _listed_paths():
            try:
                resolve(path)
            except Resolver404:
                self.fail('{} does not resolve (add a route or drop from smoke list)'.format(path))

    def test_ladder_results_not_task_group_catch_all(self):
        from games.views.new_ui import new_section_results_page, new_task_group_page

        match = resolve('/ladder/results/')
        self.assertIs(match.func, new_section_results_page)
        self.assertIsNot(match.func, new_task_group_page)
        self.assertEqual(match.kwargs.get('game_id'), 'ladder')
        self.assertNotIn('task_group_number', match.kwargs)
