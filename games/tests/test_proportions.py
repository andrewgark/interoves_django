from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.models import Attempt, CheckerType, Game, HTMLPage, Project, Task, TaskGroup, Team
from games.proportions import build_proportions_chips_for_tasks, parse_proportions_pair
from games.views.attempt_views import check_attempt


class _FakeTask:
    def __init__(self, task_type, answer, pk=1):
        self.task_type = task_type
        self.answer = answer
        self.id = pk
        self.pk = pk


class ProportionsParseTest(SimpleTestCase):
    def test_parse_space_slash(self):
        self.assertEqual(parse_proportions_pair('кот / собака'), ('кот', 'собака'))

    def test_parse_slash_only(self):
        self.assertEqual(parse_proportions_pair('кот/собака'), ('кот', 'собака'))

    def test_parse_strips(self):
        self.assertEqual(parse_proportions_pair('  a / b  '), ('a', 'b'))

    def test_parse_invalid(self):
        self.assertIsNone(parse_proportions_pair(''))
        self.assertIsNone(parse_proportions_pair('no slash'))
        self.assertIsNone(parse_proportions_pair('a /'))
        self.assertIsNone(parse_proportions_pair(None))


class ProportionsChipsTest(SimpleTestCase):
    def test_build_skips_non_proportions(self):
        tasks = [
            _FakeTask('default', 'x / y', pk=10),
            _FakeTask('proportions', 'лев / тигр', pk=20),
        ]
        chips = build_proportions_chips_for_tasks(tasks)
        self.assertEqual(len(chips), 2)
        self.assertEqual(chips[0]['label'], 'лев')
        self.assertEqual(chips[1]['label'], 'тигр')
        self.assertEqual(chips[0]['task_id'], 20)
        self.assertEqual(chips[1]['task_id'], 20)

    def test_build_duplicates_and_order(self):
        tasks = [
            _FakeTask('proportions', 'a / b', pk=1),
            _FakeTask('proportions', 'a / b', pk=2),
        ]
        chips = build_proportions_chips_for_tasks(tasks)
        self.assertEqual([c['label'] for c in chips], ['a', 'a', 'b', 'b'])
        self.assertEqual([c['id'] for c in chips], [0, 1, 2, 3])
        self.assertEqual([c['task_id'] for c in chips], [1, 2, 1, 2])


def _ensure_checker_and_project():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


class ProportionsCheckerUsesAnswerTest(TestCase):
    """Пустой checker_data + заполненный answer (как в инструкции к пропорциям)."""

    @classmethod
    def setUpTestData(cls):
        _ensure_checker_and_project()
        cls.game = Game.objects.create(
            id='proportions_checker_test_game',
            name='pc',
            author='a',
            author_extra='',
        )
        cls.tg = TaskGroup.objects.create(game=cls.game, name='tg', number=1)
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='proportions',
                answer='лев / тигр',
                checker_data='',
            )
        cls.team = Team.objects.create(name='proportions_checker_test_team', visible_name='T')

    def test_ok_when_checker_data_empty_answer_set(self):
        attempt = Attempt(
            text='лев / тигр',
            task=self.task,
            team=self.team,
            time=timezone.now(),
        )
        with patch('games.views.track.track_task_change'):
            check_attempt(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'Ok')
