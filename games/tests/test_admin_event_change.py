"""Admin change pages for high-volume events must not render giant FK dropdowns."""
import re

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    Hint,
    HintAttempt,
    HTMLPage,
    Like,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
)


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


_SELECT_NAME_RE = re.compile(r'<select[^>]*\bname="([^"]+)"', re.IGNORECASE)


class AdminEventChangeViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.admin = User.objects.create_superuser('event_admin', 'admin@example.com', 'secret')
        cls.player = User.objects.create_user('event_player', 'player@example.com', 'secret')
        cls.team = Team.objects.create(name='event_team', visible_name='Event Team')
        cls.game = Game.objects.create(id='event_game', name='Event Game', author='test')
        cls.task_group = TaskGroup.objects.create(label='event_tg')
        cls.task = Task.objects.create(task_group=cls.task_group, number='1', text='Q')
        GameTaskGroup.objects.create(
            game=cls.game, task_group=cls.task_group, number='1', name='One',
        )
        for i in range(12):
            Task.objects.create(task_group=cls.task_group, number=str(i + 2), text='extra')

        cls.like = Like.manager.create(team=cls.team, task=cls.task, value=1)
        cls.hint = Hint.objects.create(task=cls.task, number='1', text='hint')
        cls.hint_attempt = HintAttempt.objects.create(team=cls.team, hint=cls.hint)
        cls.attempt = Attempt.manager.create(
            team=cls.team, task=cls.task, game=cls.game, text='ans', status='Wrong',
        )
        cls.registration = Registration.objects.create(team=cls.team, game=cls.game)

    def setUp(self):
        self.client.force_login(self.admin)

    def _select_names(self, html):
        return set(_SELECT_NAME_RE.findall(html))

    def _assert_no_fk_selects(self, response, field_names):
        self.assertEqual(response.status_code, 200)
        names = self._select_names(response.content.decode())
        for field_name in field_names:
            self.assertNotIn(
                field_name,
                names,
                'change form still renders a full <select> for {}'.format(field_name),
            )
            self.assertContains(response, 'vForeignKeyRawIdAdminField')

    def test_like_change_uses_raw_id_instead_of_dropdowns(self):
        response = self.client.get(reverse('admin:games_like_change', args=[self.like.pk]))
        self._assert_no_fk_selects(response, ('task', 'team', 'user'))

    def test_hint_attempt_change_uses_raw_id_instead_of_dropdowns(self):
        response = self.client.get(
            reverse('admin:games_hintattempt_change', args=[self.hint_attempt.pk]),
        )
        self._assert_no_fk_selects(response, ('hint', 'team', 'user'))

    def test_attempt_change_uses_raw_id_instead_of_dropdowns(self):
        response = self.client.get(reverse('admin:games_attempt_change', args=[self.attempt.pk]))
        self._assert_no_fk_selects(response, ('task', 'team', 'user', 'game'))

    def test_registration_change_uses_raw_id_instead_of_dropdowns(self):
        response = self.client.get(
            reverse('admin:games_registration_change', args=[self.registration.pk]),
        )
        self._assert_no_fk_selects(response, ('team', 'game', 'with_referent'))

    def test_like_change_query_count_does_not_grow_with_tasks(self):
        url = reverse('admin:games_like_change', args=[self.like.pk])
        with CaptureQueriesContext(connection) as first:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        baseline = len(first.captured_queries)

        for i in range(20):
            Task.objects.create(task_group=self.task_group, number='x{}'.format(i), text='more')

        with CaptureQueriesContext(connection) as second:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(second.captured_queries) - baseline,
            2,
            'like change view queries grew after adding tasks; FK dropdowns may be back',
        )
