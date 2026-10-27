from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from games.task_group.navigation import (
    neighbors_by_pk,
    play_url_for_task_group,
    replay_exit_url_for_task_group,
    replay_url_for_task_group,
    results_url_for_task_group,
    task_group_page_nav_context,
)


class TaskGroupNavigationTests(SimpleTestCase):
    def setUp(self):
        self.game = SimpleNamespace(
            id='demo',
            project_id='custom',
            no_html_name='Demo game',
            outside_name='',
            name='Demo',
        )

    def test_project_scoped_urls_share_one_shape(self):
        self.assertEqual(
            play_url_for_task_group(self.game, '2', project_base='/club'),
            '/club/games/demo/2/',
        )
        self.assertEqual(
            results_url_for_task_group(self.game, '2', project_base='/club'),
            '/club/games/demo/2/results/',
        )
        self.assertEqual(
            replay_url_for_task_group(self.game, '2', project_base='/club'),
            '/club/games/demo/2/replay/',
        )
        self.assertEqual(
            replay_exit_url_for_task_group(self.game, '2', project_base='/club'),
            '/club/games/demo/2/replay/exit/',
        )

    def test_neighbors_are_selected_by_link_primary_key(self):
        links = [
            SimpleNamespace(pk=10),
            SimpleNamespace(pk=20),
            SimpleNamespace(pk=30),
        ]

        previous, following = neighbors_by_pk(links, links[1])

        self.assertIs(previous, links[0])
        self.assertIs(following, links[2])

    def test_navigation_context_contains_labels_and_adjacent_numbers(self):
        previous = SimpleNamespace(number='1', name='One')
        following = SimpleNamespace(number='3', name='Three')

        context = task_group_page_nav_context(
            self.game,
            previous=previous,
            following=following,
            main_project_id='custom',
        )

        self.assertEqual(context['back_label'], 'К игре')
        self.assertEqual(context['task_group_pager_label'], 'Demo game')
        self.assertEqual(context['prev_task_group_number'], '1')
        self.assertEqual(context['next_task_group_name'], 'Three')

    def test_section_back_label_names_the_section_hub(self):
        ladder = SimpleNamespace(
            id='ladder', project_id='sections', no_html_name='', outside_name='', name='',
        )
        unknown = SimpleNamespace(
            id='unknown', project_id='sections', no_html_name='X', outside_name='', name='',
        )

        self.assertEqual(task_group_page_nav_context(ladder)['back_label'], 'Все Лесенки')
        self.assertEqual(task_group_page_nav_context(unknown)['back_label'], 'Все задания')
        self.assertEqual(
            task_group_page_nav_context(ladder, back_label='К заданию')['back_label'],
            'К заданию',
        )

    def test_daily_registry_provides_fallback_label(self):
        game = SimpleNamespace(
            id='salad',
            project_id='custom',
            no_html_name='',
            outside_name='',
            name='',
        )

        with patch.dict('games.task_group.navigation.SECTION_HUB_META', {'salad': {}}, clear=False):
            context = task_group_page_nav_context(game)

        self.assertEqual(context['task_group_pager_label'], 'Салатик')
