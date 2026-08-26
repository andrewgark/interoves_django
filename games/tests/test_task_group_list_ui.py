from django.template.loader import render_to_string
from django.test import SimpleTestCase

from games.models import Game


class TaskGroupListUiTests(SimpleTestCase):
    def _render_row(self, *, solved=False):
        game = Game(
            id='task-list-ui',
            project_id='sections',
            name='Список заданий',
            author='Interoves',
            is_ready=True,
        )
        return render_to_string(
            'new/_task_group_rows.html',
            {
                'game': game,
                'team': None,
                'task_group_rows': [{
                    'number': '1',
                    'play_url': '/play/',
                    'results_url': '/results/',
                    'title': 'Задание №1',
                    'is_fully_solved': solved,
                    'row_class': 'new-task--solved' if solved else '',
                }],
            },
        )

    def test_unsolved_row_has_no_redundant_open_button(self):
        html = self._render_row()

        self.assertNotIn('Открыть', html)
        self.assertIn('class="new-task__body" href="/play/"', html)

    def test_results_are_a_compact_link_and_solved_status_remains(self):
        html = self._render_row(solved=True)

        self.assertIn(
            '<a class="new-task__results" href="/results/">Результаты</a>',
            html,
        )
        self.assertNotIn('new-btn new-btn--ghost new-btn--mini new-task__results', html)
        self.assertIn('new-pill new-pill--ok', html)
