import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from games.models import Task


class TaskResultsMaxPointsTests(SimpleTestCase):
    def test_default_uses_task_multiplier(self):
        t = Task(task_type='default', points=3)
        self.assertEqual(t.get_results_max_points(), 3.0)

    def test_wall_multiplies_wall_max_by_task_points(self):
        t = Task(task_type='wall', points=2)
        with patch.object(t, 'get_wall', return_value=MagicMock(max_points=10)):
            self.assertEqual(t.get_results_max_points(), 20.0)

    def test_replacements_lines_json_checker_rows(self):
        checker_data = json.dumps({'lines': [['a'], ['b'], ['c']]})
        t = Task(task_type='replacements_lines', points=2, checker_data=checker_data, text='')
        self.assertEqual(t.get_results_max_points(), 6.0)
