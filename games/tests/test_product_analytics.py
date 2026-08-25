import json
from io import StringIO

from allauth.account.signals import user_signed_up
from django.contrib.auth.models import User
from django.core.management import call_command
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from games.analytics import (
    is_task_completion_state,
    register_completed_game,
    register_started_game,
)
from games.context_processors import analytics_bootstrap
from games.models import (
    CheckerType,
    Attempt,
    ChainTaskState,
    Game,
    HTMLPage,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
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
        state = PlayerAnalyticsState.objects.get(user=self.user)
        self.assertIsNotNone(state.activated_at)
        activation = next(item for item in goals[2] if item['goal'] == 'activated_player')
        ack = self.client.post(
            reverse('analytics_goal_ack'),
            {'token': activation['ack']['token']},
        )
        self.assertEqual(ack.status_code, 200)
        state.refresh_from_db()
        self.assertIsNotNone(state.activation_goal_acked_at)

    def test_completion_repeats_until_metrika_ack_then_stops(self):
        game, task = self._make_supported_task('ladder', 'raddle', 1)

        first = register_completed_game(user=self.user, task=task, game=game)
        second = register_completed_game(user=self.user, task=task, game=game)

        self.assertEqual([item['goal'] for item in first], ['game_complete'])
        self.assertEqual([item['goal'] for item in second], ['game_complete'])
        ack = self.client.post(
            reverse('analytics_goal_ack'),
            {'token': first[0]['ack']['token']},
        )
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(register_completed_game(user=self.user, task=task, game=game), [])
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)

    def test_word_salad_completion_uses_existing_game_complete_pipeline(self):
        game, task = self._make_supported_task('salad', 'word_salad', 1)
        task.checker_data = json.dumps({
            'grid': list('ABCDEFGHIJKLMNOP'),
            'words': ['ABCD', 'EFGH'],
        })
        task.save(update_fields=['checker_data'])
        complete_state = json.dumps({'solved_indices': [0, 1], 'active': []})

        self.assertTrue(is_task_completion_state(task, complete_state))
        self.assertFalse(is_task_completion_state(
            task,
            json.dumps({'solved_indices': [0]}),
        ))
        self.assertFalse(is_task_completion_state(
            task,
            json.dumps({'solved_indices': [14, 15]}),
        ))

        goals = register_completed_game(user=self.user, task=task, game=game)

        self.assertEqual([item['goal'] for item in goals], ['game_complete'])
        event = goals[0]
        self.assertEqual(event['params']['game'], 'salad')
        self.assertTrue(PlayerCompletedGame.objects.filter(
            user=self.user,
            game=game,
            game_kind='salad',
        ).exists())

    def test_current_completed_state_is_not_swallowed_by_history_backfill(self):
        game, task = self._make_supported_task('alphabetty', 'alphabetty', 1)
        ChainTaskState.objects.create(
            user=self.user,
            task=task,
            game=game,
            game_mode='general',
            state=json.dumps({'won': True}),
        )

        goals = register_completed_game(user=self.user, task=task, game=game)

        self.assertEqual([item['goal'] for item in goals], ['game_complete'])
        self.assertFalse(PlayerCompletedGame.objects.get(user=self.user).is_backfilled)

    def test_game_start_is_generic_unique_and_repeats_until_metrika_ack(self):
        game, task = self._make_supported_task('walls-custom', 'wall', 1)

        first = register_started_game(user=self.user, task=task, game=game)
        retry = register_started_game(user=self.user, task=task, game=game)

        self.assertEqual([item['goal'] for item in first], ['game_start'])
        self.assertEqual(first[0]['params'], {'game': 'walls-custom', 'game_id': str(task.task_group_id)})
        self.assertEqual(first[0]['key'], retry[0]['key'])
        self.assertEqual(PlayerStartedGame.objects.filter(user=self.user).count(), 1)

        ack = self.client.post(
            reverse('analytics_goal_ack'),
            {'token': first[0]['ack']['token']},
        )
        self.assertEqual(ack.status_code, 200)
        self.assertTrue(ack.json()['ok'])
        self.assertIsNotNone(PlayerStartedGame.objects.get(user=self.user).metrika_acked_at)
        self.assertEqual(register_started_game(user=self.user, task=task, game=game), [])

    def test_team_start_is_attributed_to_authenticated_analytics_user(self):
        game, task = self._make_supported_task('generic-team-game', 'default', 1)

        goals = register_started_game(
            team=self.team,
            analytics_user=self.user,
            task=task,
            game=game,
        )

        self.assertEqual([item['goal'] for item in goals], ['game_start'])
        row = PlayerStartedGame.objects.get()
        self.assertEqual(row.user, self.user)
        self.assertIsNone(row.team)

    def test_historical_backfill_keeps_original_time_and_suppresses_old_goal(self):
        game, task = self._make_supported_task('historical-game', 'default', 1)
        attempt = Attempt.manager.create(
            user=self.user,
            task=task,
            game=game,
            text='wrong',
            status='Wrong',
            points=0,
        )

        call_command('backfill_player_started_games', verbosity=0)

        row = PlayerStartedGame.objects.get(user=self.user, game=game)
        self.assertTrue(row.is_backfilled)
        self.assertEqual(row.started_at, attempt.time)
        self.assertEqual(register_started_game(user=self.user, task=task, game=game), [])

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
        state = PlayerAnalyticsState.objects.get(user=self.user)
        self.assertIsNotNone(state.activated_at)
        self.assertTrue(state.activation_is_backfilled)

    def test_signup_is_durable_until_signed_ack(self):
        user = User.objects.create_user(username='new-analytics-user', email='new@example.com')
        request = RequestFactory().get('/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request.user = user

        user_signed_up.send(sender=User, request=request, user=user)
        first = analytics_bootstrap(request)['pending_analytics_goals']
        second = analytics_bootstrap(request)['pending_analytics_goals']

        self.assertEqual([item['goal'] for item in first], ['signup'])
        self.assertEqual([item['goal'] for item in second], ['signup'])
        ack = self.client.post(
            reverse('analytics_goal_ack'),
            {'token': first[0]['ack']['token']},
        )
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(analytics_bootstrap(request)['pending_analytics_goals'], [])

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
        self.assertIn('ack', first.json()['analytics_events'][0])
        self.assertNotIn('analytics_events', third.json())

    def test_ticket_purchase_signed_ack_stops_retries(self):
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

        status_url = reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket.id})
        event = self.client.get(status_url).json()['analytics_events'][0]
        ack = self.client.post(reverse('analytics_goal_ack'), {'token': event['ack']['token']})

        self.assertEqual(ack.status_code, 200)
        self.assertNotIn('analytics_events', self.client.get(status_url).json())

    def test_ticket_purchase_is_retried_on_every_page_until_signed_ack(self):
        ticket = TicketRequest.objects.create(
            team=self.team,
            created_by=self.user,
            money=2000,
            tickets=1,
            status='Accepted',
            currency='RUB',
            payment_provider='yookassa',
            merchant='ru_self_employed',
            purchase_goal_queued_at=timezone.now(),
        )
        request = RequestFactory().get('/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request.user = self.user

        other_user = User.objects.create_user(username='other-analytics-user')
        Profile.objects.create(
            user=other_user,
            first_name='Other',
            last_name='User',
            team_on=self.team,
        )
        other_request = RequestFactory().get('/')
        SessionMiddleware(lambda req: None).process_request(other_request)
        other_request.session.save()
        other_request.user = other_user

        first = analytics_bootstrap(request)['pending_analytics_goals']
        second = analytics_bootstrap(request)['pending_analytics_goals']

        self.assertEqual(analytics_bootstrap(other_request)['pending_analytics_goals'], [])
        self.assertEqual([item['goal'] for item in first], ['ticket_purchase'])
        self.assertEqual(first[0]['key'], 'ticket_purchase:{}'.format(ticket.pk))
        self.assertEqual([item['goal'] for item in second], ['ticket_purchase'])
        ack = self.client.post(first[0]['ack']['url'], {'token': first[0]['ack']['token']})
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(analytics_bootstrap(request)['pending_analytics_goals'], [])

    def test_legacy_purchase_is_retried_for_team_member(self):
        ticket = TicketRequest.objects.create(
            team=self.team,
            created_by=None,
            money=2000,
            tickets=1,
            status='Accepted',
            currency='RUB',
            payment_provider='yookassa',
            merchant='ru_self_employed',
            purchase_goal_queued_at=timezone.now(),
        )
        request = RequestFactory().get('/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request.user = self.user

        goals = analytics_bootstrap(request)['pending_analytics_goals']

        self.assertEqual([item['key'] for item in goals], [
            'ticket_purchase:{}'.format(ticket.pk),
        ])

    def test_delivery_report_lists_every_configured_goal(self):
        out = StringIO()

        call_command('report_yandex_goals', days=14, stdout=out)

        report = out.getvalue()
        for goal in (
            'game_start', 'game_complete', 'signup', 'activated_player',
            'ticket_checkout', 'ticket_purchase',
        ):
            self.assertIn(goal, report)
