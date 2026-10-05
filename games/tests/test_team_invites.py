from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

from games.models import Profile, ProfileTeamMembership, Project, Team, TeamInvite


class TeamInviteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.team = Team.objects.create(name='invite_team', visible_name='Invite Team')
        cls.captain = User.objects.create_user('invite_captain', password='secret')
        cls.friend = User.objects.create_user('invite_friend', password='secret')
        Profile.objects.create(user=cls.captain, first_name='Captain', last_name='One')
        Profile.objects.create(user=cls.friend, first_name='Friend', last_name='Two')
        cls.captain.profile.add_team_membership(cls.team, make_primary=True)

    def setUp(self):
        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)
        self.client = Client()
        self.assertTrue(self.client.login(username='invite_captain', password='secret'))

    def test_member_can_create_and_replace_invite(self):
        create_url = reverse('new_team_invite_create')
        response = self.client.post(create_url)
        self.assertEqual(response.status_code, 302)
        invite = TeamInvite.objects.get(team=self.team, revoked_at__isnull=True)
        self.assertEqual(invite.created_by, self.captain)

        self.client.post(create_url)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)
        self.assertEqual(TeamInvite.objects.filter(team=self.team, revoked_at__isnull=True).count(), 1)

    def test_friend_can_accept_invite_and_becomes_member(self):
        invite = TeamInvite.objects.create(team=self.team, token='invite-token-for-test')
        self.client.logout()
        self.assertTrue(self.client.login(username='invite_friend', password='secret'))
        response = self.client.post(
            reverse('new_team_invite_accept', kwargs={'token': invite.token}),
        )
        self.assertEqual(response.status_code, 302)
        self.friend.profile.refresh_from_db()
        self.assertEqual(self.friend.profile.team_on_id, self.team.pk)
        self.assertTrue(
            ProfileTeamMembership.objects.filter(profile=self.friend.profile, team=self.team).exists()
        )

    def test_get_does_not_join_and_anonymous_user_is_sent_to_login(self):
        invite = TeamInvite.objects.create(team=self.team, token='get-only-token')
        self.client.logout()
        response = self.client.get(
            reverse('new_team_invite', kwargs={'token': invite.token}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Войти и вступить')
        self.assertContains(response, 'login=1&amp;next=%2Fteam%2Finvite%2Fget-only-token%2F')
        rendered = response.content.decode('utf-8')
        self.assertGreater(
            rendered.count('next=%2Fteam%2Finvite%2Fget-only-token%2F'),
            1,
        )
        external = self.client.get(
            reverse('new_team_invite', kwargs={'token': invite.token}) + '?next=https://example.com/',
        )
        external_rendered = external.content.decode('utf-8')
        self.assertNotIn('next=https%3A%2F%2Fexample.com%2F', external_rendered)
        self.friend.profile.refresh_from_db()
        self.assertIsNone(self.friend.profile.team_on_id)

    def test_revoked_invite_is_unavailable(self):
        invite = TeamInvite.objects.create(team=self.team, token='revoked-token')
        invite.revoked_at = invite.created_at
        invite.save(update_fields=['revoked_at'])
        response = self.client.get(
            reverse('new_team_invite', kwargs={'token': invite.token}),
        )
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'недействительна', status_code=404)

    def test_team_page_shows_invite_controls(self):
        TeamInvite.objects.create(team=self.team, token='page-token')
        response = self.client.get(reverse('new_team'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Пригласить друзей')
        self.assertContains(response, '/team/invite/page-token/')
