from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from games.models import (
    Attempt,
    BugReport,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP
from games.support.services.search import parse_search_query, search


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


class SupportAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='support_team', visible_name='Support Team')
        cls.game = Game.objects.create(
            id='support_game',
            name='Support Game',
            author='test',
        )
        cls.task_group = TaskGroup.objects.create(label='Support TG')
        cls.task = Task.objects.create(task_group=cls.task_group, number='1', text='Q', checker_data='')
        cls.staff = User.objects.create_user('support_staff', 'staff@example.com', 'secret')
        cls.other = User.objects.create_user('support_other', 'other@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='S', last_name='T')
        Profile.objects.create(user=cls.other, first_name='O', last_name='R')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        Attempt.manager.create(
            team=cls.team,
            task=cls.task,
            game=cls.game,
            text='answer',
            status='Ok',
            points=10,
        )

    def setUp(self):
        self.client = Client()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('support:hub'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_user_without_group_gets_403(self):
        self.assertTrue(self.client.login(username='support_other', password='secret'))
        response = self.client.get(reverse('support:hub'))
        self.assertEqual(response.status_code, 403)

    def test_group_member_can_open_hub(self):
        self.assertTrue(self.client.login(username='support_staff', password='secret'))
        response = self.client.get(reverse('support:hub'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Support Console')

    def test_superuser_can_open_hub(self):
        admin = User.objects.create_superuser('support_admin', 'admin@example.com', 'secret')
        Profile.objects.create(user=admin, first_name='A', last_name='D')
        self.assertTrue(self.client.login(username='support_admin', password='secret'))
        response = self.client.get(reverse('support:hub'))
        self.assertEqual(response.status_code, 200)


class SupportSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='alpha_squad', visible_name='Alpha Squad')

    def test_parse_team_prefix(self):
        self.assertEqual(parse_search_query('team:alpha'), ('team', 'alpha'))

    def test_search_finds_team(self):
        hits = search('alpha')
        kinds = [h.kind for h in hits]
        self.assertIn('team', kinds)


class SupportPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='page_team', visible_name='Page Team')
        cls.game = Game.objects.create(
            id='page_game',
            name='Page Game',
            author='test',
        )
        cls.task_group = TaskGroup.objects.create(label='Page TG')
        cls.task = Task.objects.create(task_group=cls.task_group, number='2', text='Q2', checker_data='')
        cls.staff = User.objects.create_user('page_staff', 'page@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='P', last_name='S')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        Attempt.manager.create(
            team=cls.team,
            task=cls.task,
            game=cls.game,
            text='hello support',
            status='Wrong',
            points=0,
        )

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='page_staff', password='secret'))

    def test_actor_team_page(self):
        url = reverse('support:actor_team', kwargs={'team_name': 'page_team'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page Team')
        self.assertContains(response, 'hello support')

    def test_game_dashboard(self):
        url = reverse('support:game', kwargs={'game_id': 'page_game'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'page_game')
        self.assertContains(response, 'hello support')

    def test_hub_search_team(self):
        response = self.client.get(reverse('support:hub'), {'q': 'page_team'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page Team')


class SupportPreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='preview_team', visible_name='Preview Team')
        cls.game = Game.objects.create(id='preview_game', name='Preview Game', author='test')
        cls.task_group = TaskGroup.objects.create(label='Preview TG')
        GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.task_group,
            number='1',
            name='First TG',
        )
        cls.task = Task.objects.create(task_group=cls.task_group, number='1', text='Preview Q', checker_data='')
        cls.staff = User.objects.create_user('preview_staff', 'preview@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='P', last_name='V')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        Attempt.manager.create(
            team=cls.team,
            task=cls.task,
            game=cls.game,
            text='preview answer',
            status='Ok',
            points=5,
        )

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='preview_staff', password='secret'))

    def test_preview_game_lists_task_groups(self):
        url = reverse('support:preview_game', kwargs={'game_id': 'preview_game'})
        response = self.client.get(url, {'team': 'preview_team', 'mode': 'team'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First TG')
        self.assertContains(response, 'Открыть preview')

    def test_preview_task_group_shows_actor_state(self):
        url = reverse(
            'support:preview_task_group',
            kwargs={'game_id': 'preview_game', 'task_group_number': '1'},
        )
        response = self.client.get(url, {'team': 'preview_team', 'mode': 'team'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Support preview')
        self.assertContains(response, 'Preview Team')
        self.assertContains(response, 'Preview Q')

    def test_preview_requires_actor(self):
        url = reverse(
            'support:preview_task_group',
            kwargs={'game_id': 'preview_game', 'task_group_number': '1'},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class SupportPendingActionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='pending_team', visible_name='Pending Team', tickets=0)
        cls.game = Game.objects.create(id='pending_game', name='Pending Game', author='test')
        cls.task_group = TaskGroup.objects.create(label='Pending TG')
        cls.task = Task.objects.create(task_group=cls.task_group, number='1', text='Q', checker_data='')
        cls.staff = User.objects.create_user('pending_staff', 'pending@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='P', last_name='S')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        cls.attempt = Attempt.manager.create(
            team=cls.team,
            task=cls.task,
            game=cls.game,
            text='pending answer',
            status='Pending',
            points=0,
        )
        cls.ticket = TicketRequest.objects.create(
            team=cls.team,
            tickets=2,
            money=4000,
            status='Pending',
        )
        cls.bug = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            team=cls.team,
            text='something broken',
            status='Pending',
        )

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='pending_staff', password='secret'))

    def test_pending_page_lists_items(self):
        response = self.client.get(reverse('support:pending'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Посылка #{}'.format(self.attempt.pk))
        self.assertContains(response, 'Билет #{}'.format(self.ticket.pk))
        self.assertContains(response, 'Баг #{}'.format(self.bug.pk))

    def test_set_ok_action(self):
        url = reverse('support:action')
        response = self.client.post(url, {
            'kind': 'attempt',
            'id': self.attempt.pk,
            'action': 'set_ok',
            'next': reverse('support:pending'),
        })
        self.assertEqual(response.status_code, 302)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, 'Ok')

    def test_ticket_accept_action(self):
        url = reverse('support:action')
        response = self.client.post(url, {
            'kind': 'ticket',
            'id': self.ticket.pk,
            'action': 'ticket_accept',
            'next': reverse('support:pending'),
        })
        self.assertEqual(response.status_code, 302)
        self.ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(self.ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 2)

    def test_bug_reviewed_action(self):
        url = reverse('support:action')
        response = self.client.post(url, {
            'kind': 'bug',
            'id': self.bug.pk,
            'action': 'bug_reviewed',
            'next': reverse('support:pending'),
        })
        self.assertEqual(response.status_code, 302)
        self.bug.refresh_from_db()
        self.assertEqual(self.bug.status, 'Reviewed')


class SupportPhase4Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='live_team', visible_name='Live Team')
        cls.stuck_team = Team.objects.create(name='stuck_team', visible_name='Stuck Team')
        now = timezone.now()
        cls.game = Game.objects.create(
            id='live_game',
            name='Live Game',
            author='test',
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
        )
        cls.task_group = TaskGroup.objects.create(label='Live TG')
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            text='Live Q',
            checker_data='',
            task_type='replacements_lines',
        )
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.task_group, number='1', name='TG1')
        cls.staff = User.objects.create_user('live_staff', 'live@example.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='L', last_name='S')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)
        Attempt.manager.create(
            team=cls.team,
            task=cls.task,
            game=cls.game,
            text='{"line_index":0}',
            status='Partial',
            points=1,
        )
        from games.models import Registration
        Registration.objects.create(game=cls.game, team=cls.stuck_team)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='live_staff', password='secret'))

    def test_live_page(self):
        response = self.client.get(reverse('support:live'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'live_game')

    def test_live_feed_json(self):
        response = self.client.get(reverse('support:live_feed_json'), {'hours': 2})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('rows', data)

    def test_stuck_teams_on_game_page(self):
        url = reverse('support:game', kwargs={'game_id': 'live_game'})
        response = self.client.get(url, {'stuck_minutes': 30})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stuck Team')

    def test_chain_page(self):
        attempt = Attempt.manager.filter(team=self.team, game=self.game).first()
        url = reverse('support:chain', kwargs={'attempt_id': attempt.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ChainTaskState')
        self.assertContains(response, 'chain replay')
