from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from games.analytics import register_completed_game
from games.models import (
    CheckerType,
    Game,
    HTMLPage,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)


class ProductAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name)
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.team = Team.objects.create(name='analytics-team')
        cls.user = User.objects.create_user(username='analytics-user', email='analytics@example.com', password='pw')
        cls.profile = Profile.objects.create(user=cls.user, first_name='A', last_name='B', team_on=cls.team)

    def _make_supported_task(self, game_id, task_type, number):
        game, _ = Game.objects.get_or_create(
            id=game_id,
            defaults={
                'name': game_id,
                'author': 'test',
                'project': self.project,
                'requires_ticket': False,
                'is_tournament': False,
                'is_ready': True,
            },
        )
        task_group = TaskGroup.objects.create(label='tg-{}-{}'.format(game_id, number))
        task = Task.objects.create(
            task_group=task_group,
            number='1',
            task_type=task_type,
            checker_id='equals_with_possible_spaces',
            answer='TEST',
        )
        return game, task

    def test_activation_fires_on_third_unique_completion(self):
        goals = []
        for number, pair in enumerate((
            ('ladder', 'raddle'),
            ('alphabetty', 'alphabetty'),
            ('replacements', 'replacements_lines'),
        ), start=1):
            game, task = self._make_supported_task(pair[0], pair[1], number)
            goals.append(register_completed_game(user=self.user, task=task, game=game))

        self.assertEqual([g[0]['goal'] for g in goals[:2]], ['game_complete', 'game_complete'])
        self.assertEqual([item['goal'] for item in goals[2]], ['game_complete', 'activated_player'])
        self.assertEqual(
            PlayerCompletedGame.objects.filter(user=self.user, team__isnull=True, anon_key__isnull=True).count(),
            3,
        )
        self.assertIsNotNone(PlayerAnalyticsState.objects.get(user=self.user).activated_at)

    def test_duplicate_completion_does_not_repeat_events(self):
        game, task = self._make_supported_task('ladder', 'raddle', 1)

        first = register_completed_game(user=self.user, task=task, game=game)
        second = register_completed_game(user=self.user, task=task, game=game)

        self.assertEqual([item['goal'] for item in first], ['game_complete'])
        self.assertEqual(second, [])
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)

    def test_team_mode_completion_counts_towards_authenticated_user_activation(self):
        goals = []
        for number in (1, 2, 3):
            game, task = self._make_supported_task('ladder', 'raddle', number)
            goals.append(register_completed_game(
                team=self.team,
                analytics_user=self.user,
                task=task,
                game=game,
            ))

        self.assertEqual([g[0]['goal'] for g in goals[:2]], ['game_complete', 'game_complete'])
        self.assertEqual([item['goal'] for item in goals[2]], ['game_complete', 'activated_player'])
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 3)
        self.assertEqual(PlayerCompletedGame.objects.filter(team=self.team).count(), 0)
        self.assertIsNotNone(PlayerAnalyticsState.objects.get(user=self.user).activated_at)

    def test_existing_three_completions_do_not_emit_late_activation(self):
        for number in (1, 2, 3):
            game, task = self._make_supported_task('ladder', 'raddle', number)
            PlayerCompletedGame.objects.create(
                user=self.user,
                game=game,
                task_group=task.task_group,
                game_kind='ladder',
                game_instance_id='ladder:{}'.format(task.task_group_id),
                public_game_id=str(number),
                result='solved',
            )

        game4, task4 = self._make_supported_task('alphabetty', 'alphabetty', 4)
        goals = register_completed_game(user=self.user, task=task4, game=game4)

        self.assertEqual([item['goal'] for item in goals], ['game_complete'])
        self.assertIsNotNone(PlayerAnalyticsState.objects.get(user=self.user).activated_at)

    def test_ticket_purchase_goal_repeats_until_ack_then_stops(self):
        ticket = TicketRequest.objects.create(
            team=self.team,
            money=2000,
            tickets=1,
            status='Accepted',
            currency='RUB',
            payment_provider='yookassa',
            merchant='ru_self_employed',
        )
        self.client.force_login(self.user)

        url = reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket.id})
        first = self.client.get(url)
        second = self.client.get(url)
        ack = self.client.post(url, {'analytics_ack': 'ticket_purchase:{}'.format(ticket.id)})
        third = self.client.get(url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual([item['goal'] for item in first.json().get('analytics_events', [])], ['ticket_purchase'])
        self.assertEqual([item['goal'] for item in second.json().get('analytics_events', [])], ['ticket_purchase'])
        self.assertNotIn('analytics_events', third.json())
