import json
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from games.models import Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup, Team
from games.views.attempt_views import check_attempt


def _setup_db():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    CheckerType.objects.get_or_create(pk='html_forms')


class HtmlFormsTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        cls.game = Game.objects.create(id='html_forms_test', name='html forms', author='test', author_extra='')
        cls.team = Team.objects.create(name='html_forms_team', visible_name='T')
        with patch('games.views.track.track_task_change'):
            cls.group = TaskGroup.objects.create(label='html forms group', points=1)
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number='1', name='html forms')
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                task_type='html_forms',
                checker_id='html_forms',
                points=2,
                text='<p>{{ html_form:first }} {{ html_form:second }}</p>',
                checker_data=json.dumps({
                    'forms': [
                        {'key': 'first', 'answer': 'alpha beta'},
                        {'key': 'second', 'answers': ['gamma', 'гамма']},
                    ],
                }),
            )

    def _attempt(self, key, text):
        return Attempt(
            task=self.task,
            team=self.team,
            game=self.game,
            time=timezone.now(),
            text=json.dumps({'form_key': key, 'text': text}, ensure_ascii=False),
        )

    def test_embedded_forms_accumulate_points_in_any_order(self):
        first = self._attempt('second', 'гамма')
        self.assertTrue(check_attempt(first))
        first.refresh_from_db()
        self.assertEqual(first.status, 'Partial')
        self.assertEqual(float(first.points), 2.0)

        second = self._attempt('first', 'alpha,   beta')
        self.assertTrue(check_attempt(second))
        second.refresh_from_db()
        self.assertEqual(second.status, 'Ok')
        self.assertEqual(float(second.points), 4.0)

        state = ChainTaskState.objects.get(task=self.task, team=self.team, game=self.game)
        payload = json.loads(state.state)
        self.assertEqual(payload['solved_keys'], ['first', 'second'])
        self.assertEqual(self.task.get_results_max_points(), 4)
