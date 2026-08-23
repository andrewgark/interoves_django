from types import SimpleNamespace

from django.test import SimpleTestCase

from games.task_titles import task_display_name, task_group_page_title


class TaskTitlesTests(SimpleTestCase):
    def test_numbered_edition_uses_page_title(self):
        game = SimpleNamespace(pk='ladder', outside_name='Лесенка', name='Лесенка')
        placement = SimpleNamespace(number='45', name='Лесенка #45')
        task = SimpleNamespace(pk=123, task_group_id=10, number='1')

        self.assertEqual(task_group_page_title(game, placement), 'Лесенка №45')
        self.assertEqual(
            task_display_name(game, task, placement=placement),
            'Лесенка №45',
        )

    def test_alphabetty_uses_numbered_edition_title(self):
        game = SimpleNamespace(pk='alphabetty', outside_name='Алфавитка', name='Алфавитка')
        placement = SimpleNamespace(number='12', name='Алфавитка #12')
        task = SimpleNamespace(pk=4, task_group_id=8, number='1')

        self.assertEqual(task_group_page_title(game, placement), 'Алфавитка №12')
        self.assertEqual(
            task_display_name(game, task, placement=placement),
            'Алфавитка №12',
        )

    def test_word_salad_uses_numbered_edition_title(self):
        game = SimpleNamespace(pk='salad', outside_name='Салатик', name='Салатик')
        placement = SimpleNamespace(number='3', name='Салатик #3')
        task = SimpleNamespace(pk=9, task_group_id=4, number='1')

        self.assertEqual(task_group_page_title(game, placement), 'Салатик №3')
        self.assertEqual(
            task_display_name(game, task, placement=placement),
            'Салатик №3',
        )

    def test_regular_task_includes_group_and_task_numbers(self):
        game = SimpleNamespace(pk='des171', outside_name='Десяточка 171', name='Десяточка 171')
        placement = SimpleNamespace(number='2', name='<b>Мнемосина</b>')
        task = SimpleNamespace(pk=6385, task_group_id=20, number='1.10')

        self.assertEqual(
            task_display_name(game, task, placement=placement),
            '2. Мнемосина (1.10)',
        )

    def test_html_entities_are_plain_text(self):
        game = SimpleNamespace(pk='des171', outside_name='Десяточка 171', name='Десяточка 171')
        placement = SimpleNamespace(number='4', name='&#127812; <i>Грибы</i>')
        task = SimpleNamespace(pk=1, task_group_id=20, number='2')

        self.assertEqual(
            task_display_name(game, task, placement=placement),
            '4. 🍄 Грибы (2)',
        )

    def test_unplaced_task_keeps_number_fallback(self):
        game = SimpleNamespace(pk='game', outside_name='Game', name='Game')
        task = SimpleNamespace(pk=99, task_group_id=None, number='7')

        self.assertEqual(task_display_name(game, task), '#7')
