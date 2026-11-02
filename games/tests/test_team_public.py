from allauth.socialaccount.models import SocialApp
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from games.community.stats import team_stats
from games.models import (
    DailySolveTiming,
    Game,
    PlayerCompletedGame,
    Profile,
    Project,
    TaskGroup,
    Team,
    TeamInvite,
)


def _social_apps():
    site = Site.objects.get_current()
    for provider in ('google', 'yandex', 'vk'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class TeamPublicPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.team = Team.objects.create(name='tp_team', visible_name='Сфинксы')
        cls.other_team = Team.objects.create(name='tp_other', visible_name='Ёжики')
        cls.member = User.objects.create_user('tp_member', password='secret')
        cls.shy = User.objects.create_user('tp_shy', password='secret')
        cls.banned = User.objects.create_user('tp_banned', password='secret')
        cls.outsider = User.objects.create_user('tp_outsider', password='secret')
        cls.member_profile = Profile.objects.create(
            user=cls.member, first_name='Анна', last_name='Петрова',
            avatar_url='https://example.com/anna.jpg', telegram_handle='anna_tg',
        )
        cls.shy_profile = Profile.objects.create(
            user=cls.shy, first_name='Тихон', last_name='Скромный',
            avatar_url='https://example.com/shy.jpg', page_hidden=True,
        )
        cls.banned_profile = Profile.objects.create(
            user=cls.banned, first_name='Скрытый', last_name='Админом', is_hidden=True,
        )
        Profile.objects.create(user=cls.outsider, first_name='Вера', last_name='Зорина')
        cls.member_profile.add_team_membership(cls.other_team, make_primary=True)
        cls.member_profile.add_team_membership(cls.team)
        cls.shy_profile.add_team_membership(cls.team, make_primary=True)
        cls.banned_profile.add_team_membership(cls.team, make_primary=True)

    def setUp(self):
        _social_apps()

    def url(self, team=None):
        return reverse('new_team_public', kwargs={'public_id': (team or self.team).public_id})

    def client_for(self, username):
        client = Client()
        self.assertTrue(client.login(username=username, password='secret'))
        return client

    def test_roster_respects_member_privacy(self):
        response = Client().get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Сфинксы', body)
        self.assertIn('href="/u/{}/"'.format(self.member_profile.public_id), body)
        self.assertIn('https://example.com/anna.jpg', body)
        # A hidden page keeps the name but loses the link and the photo.
        self.assertIn('Тихон Скромный', body)
        self.assertNotIn('/u/{}/'.format(self.shy_profile.public_id), body)
        self.assertNotIn('https://example.com/shy.jpg', body)
        # Admin-hidden profiles are not listed; contacts never are.
        self.assertNotIn('Скрытый', body)
        self.assertNotIn('anna_tg', body)
        self.assertIn('2 участника', body)
        self.assertNotIn('Попроситься', body)

    def test_hidden_team_is_visible_to_members_only(self):
        Team.objects.filter(pk=self.team.pk).update(is_hidden=True)
        self.assertEqual(Client().get(self.url()).status_code, 404)
        self.assertEqual(self.client_for('tp_outsider').get(self.url()).status_code, 404)
        self.assertEqual(self.client_for('tp_member').get(self.url()).status_code, 200)
        self.assertEqual(Client().get('/t/nosuchid/').status_code, 404)

    def test_member_can_make_team_active_and_returns_to_page(self):
        client = self.client_for('tp_member')
        body = client.get(self.url()).content.decode()
        self.assertIn('Сделать активной', body)
        response = client.post(reverse('new_team_set_primary'), {'team': self.team.pk, 'next': self.url()})
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.member_profile.refresh_from_db()
        self.assertEqual(self.member_profile.team_on_id, self.team.pk)
        self.assertIn('Ваша активная команда', client.get(self.url()).content.decode())

    def test_member_creates_invite_for_non_active_team(self):
        client = self.client_for('tp_member')
        response = client.post(reverse('new_team_invite_create'), {'team': self.team.pk, 'next': self.url()})
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        invite = TeamInvite.objects.get(team=self.team, revoked_at__isnull=True)
        self.assertFalse(TeamInvite.objects.filter(team=self.other_team).exists())
        self.assertIn(invite.token, client.get(self.url()).content.decode())

    def test_non_member_cannot_create_invite_for_team(self):
        response = self.client_for('tp_outsider').post(
            reverse('new_team_invite_create'), {'team': self.team.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(TeamInvite.objects.filter(team=self.team).exists())

    def test_outsider_requests_to_join_without_switching_active_team(self):
        client = self.client_for('tp_outsider')
        self.assertIn('Попроситься в команду', client.get(self.url()).content.decode())
        response = client.post(reverse('new_team_request_join'), {
            'name': self.team.name, 'next': self.url(), 'make_primary': '0',
        })
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        profile = Profile.objects.get(user=self.outsider)
        self.assertEqual(profile.team_requested_id, self.team.pk)
        self.assertFalse(profile.join_accept_as_primary)
        self.assertIn('Заявка отправлена', client.get(self.url()).content.decode())

    def test_member_sees_and_confirms_join_request_on_team_page(self):
        outsider = Profile.objects.get(user=self.outsider)
        outsider.team_requested = self.team
        outsider.join_accept_as_primary = False
        outsider.save(update_fields=['team_requested', 'join_accept_as_primary'])
        client = self.client_for('tp_member')
        body = client.get(self.url()).content.decode()
        self.assertIn('Хотят в команду', body)
        self.assertIn('Вера Зорина', body)
        self.assertNotIn('Хотят в команду', Client().get(self.url()).content.decode())
        response = client.post(
            '/confirm_user_joining_team/{}/'.format(self.outsider.pk),
            {'next': self.url()}, HTTP_REFERER='http://testserver' + self.url(),
        )
        self.assertEqual(response['Location'], 'http://testserver' + self.url())
        outsider.refresh_from_db()
        self.assertIsNone(outsider.team_requested_id)
        self.assertEqual(outsider.team_on_id, self.team.pk)

    def test_next_redirect_only_to_team_pages(self):
        response = self.client_for('tp_member').post(
            reverse('new_team_set_primary'), {'team': self.team.pk, 'next': 'https://evil.example/'},
        )
        self.assertEqual(response['Location'], reverse('new_team'))

    def test_player_page_links_teams(self):
        body = Client().get(
            reverse('new_player_profile', kwargs={'public_id': self.member_profile.public_id}),
        ).content.decode()
        self.assertIn('href="{}"'.format(self.url()), body)


class TeamStatsTests(TestCase):
    def test_team_stats_use_team_rows_and_fastest_timing(self):
        Project.objects.get_or_create(pk='main', defaults={})
        game, _ = Game.objects.get_or_create(
            id='walls',
            defaults={'name': 'Стенки', 'author': 'test', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
        )
        team = Team.objects.create(name='ts_team')
        user = User.objects.create_user('ts_user')
        group = TaskGroup.objects.create(label='ts_group')
        PlayerCompletedGame.objects.create(
            team=team, game=game, task_group=group, game_kind='walls', game_instance_id='ts-team',
        )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=group, game_kind='walls', game_instance_id='ts-user',
        )
        for key, ms in (('first', 200_000), ('second', 80_000)):
            DailySolveTiming.objects.create(
                game=game, task_group=group, team=team, team_timing_key=key,
                status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=ms, frozen_ms=ms,
            )
        stats = team_stats(team, now=timezone.now())
        [row] = stats.games
        self.assertEqual(row.played, 1)
        self.assertEqual(row.median_display, '1:20')
        self.assertEqual(stats.current_day_streak, 1)


class TeamResultsLinkTests(TestCase):
    def test_team_rows_link_to_team_page(self):
        team = Team.objects.create(name='rl_team', visible_name='Сфинксы')
        html = render_to_string('site/partials/results_rows.html', {'teams_sorted': [team]})
        self.assertIn('href="/t/{}/">Сфинксы</a>'.format(team.public_id), html)
        rows = [{'actor': team, 'actor_label': 'Сфинксы', 'actor_kind': 'team', 'cells': {}, 'cell_meta': {}}]
        html = render_to_string('site/partials/aggregate_results_rows.html', {'aggregate_rows': rows})
        self.assertIn('href="/t/{}/">Сфинксы</a>'.format(team.public_id), html)
