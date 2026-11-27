from types import SimpleNamespace

from django.test import SimpleTestCase

from games.site.http.tags.filters import task_item_start_ids


def _tasks(*numbers):
    return [SimpleNamespace(id=index, number=number) for index, number in enumerate(numbers)]


class TaskItemStartIdsTests(SimpleTestCase):
    def test_parts_of_one_item_stay_together(self):
        # 1.1 1.2 | 2.1 2.2 | 3
        tasks = _tasks('1.1', '1.2', '2.1', '2.2', '3')
        self.assertEqual(task_item_start_ids(tasks), {2, 4})

    def test_plain_numbers_each_open_an_item(self):
        self.assertEqual(task_item_start_ids(_tasks('1', '2', '3')), {1, 2})

    def test_empty(self):
        self.assertEqual(task_item_start_ids([]), set())
