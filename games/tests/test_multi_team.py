"""Многокомандность: членства, активная команда, выход из одной команды, отклонение заявки."""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import Profile, ProfileTeamMembership, Project, Team


class MultiTeamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.team_a = Team.objects.create(
            name='multi_team_a',
            visible_name='Multi Alpha',
            join_password='aaaabbbb',
        )
        cls.team_b = Team.objects.create(
            name='multi_team_b',
            visible_name='Multi Beta',
            join_password='ccccdddd',
        )
        cls.user = User.objects.create_user('multi_team_user', 'multi_team_user@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='M', last_name='T')

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='multi_team_user', password='secret'))

    def test_two_teams_via_password_both_memberships(self):
        url = reverse('new_team_join_by_password')
        self.client.post(url, {'name': 'Multi Alpha', 'password': 'aaaabbbb'})
        self.client.post(url, {'name': 'Multi Beta', 'password': 'ccccdddd'})
        self.user.profile.refresh_from_db()
        self.assertEqual(ProfileTeamMembership.objects.filter(profile=self.user.profile).count(), 2)
        self.assertEqual(self.user.profile.team_on_id, self.team_b.pk)

    def test_join_second_with_make_primary_zero_keeps_first_active(self):
        url = reverse('new_team_join_by_password')
        self.client.post(
            url,
            {'name': 'Multi Alpha', 'password': 'aaaabbbb', 'make_primary': '1'},
        )
        self.client.post(
            url,
            {'name': 'Multi Beta', 'password': 'ccccdddd', 'make_primary': '0'},
        )
        self.user.profile.refresh_from_db()
        self.assertEqual(ProfileTeamMembership.objects.filter(profile=self.user.profile).count(), 2)
        self.assertEqual(self.user.profile.team_on_id, self.team_a.pk)

    def test_set_primary_team(self):
        p = self.user.profile
        p.add_team_membership(self.team_a, make_primary=True)
        p.add_team_membership(self.team_b, make_primary=False)
        p.refresh_from_db()
        self.assertEqual(p.team_on_id, self.team_a.pk)
        self.client.post(reverse('new_team_set_primary'), {'team': self.team_b.pk})
        p.refresh_from_db()
        self.assertEqual(p.team_on_id, self.team_b.pk)

    def test_quit_one_team_leaves_other(self):
        p = self.user.profile
        p.add_team_membership(self.team_a, make_primary=True)
        p.add_team_membership(self.team_b, make_primary=False)
        self.client.post(
            reverse('quit_from_team'),
            {'team': self.team_a.pk, 'next': '/team/'},
        )
        p.refresh_from_db()
        self.assertEqual(p.team_on_id, self.team_b.pk)
        self.assertEqual(ProfileTeamMembership.objects.filter(profile=p).count(), 1)

    def test_join_same_team_twice_is_idempotent(self):
        p = self.user.profile
        p.add_team_membership(self.team_a, make_primary=True)
        self.client.post(
            reverse('new_team_join_by_password'),
            {'name': 'Multi Alpha', 'password': 'aaaabbbb'},
        )
        p.refresh_from_db()
        self.assertEqual(ProfileTeamMembership.objects.filter(profile=p, team=self.team_a).count(), 1)
        self.assertEqual(p.team_on_id, self.team_a.pk)

    def test_reject_application_keeps_other_team(self):
        captain = User.objects.create_user('mt_captain', 'mt_captain@example.com', 'secret')
        applicant = User.objects.create_user('mt_applicant', 'mt_applicant@example.com', 'secret')
        Profile.objects.create(user=captain, first_name='C', last_name='P')
        Profile.objects.create(user=applicant, first_name='A', last_name='P')
        captain.profile.add_team_membership(self.team_a, make_primary=True)
        applicant.profile.add_team_membership(self.team_b, make_primary=True)
        applicant.profile.team_requested = self.team_a
        applicant.profile.save(update_fields=['team_requested'])
        self.client.logout()
        self.assertTrue(self.client.login(username='mt_captain', password='secret'))
        self.client.get('/reject_user_joining_team/%d/' % applicant.pk)
        applicant.profile.refresh_from_db()
        self.assertIsNone(applicant.profile.team_requested)
        self.assertEqual(applicant.profile.team_on_id, self.team_b.pk)

    def test_confirm_respects_join_accept_as_primary_false(self):
        captain = User.objects.create_user('mt_cap2', 'mt_cap2@example.com', 'secret')
        applicant = User.objects.create_user('mt_app2', 'mt_app2@example.com', 'secret')
        Profile.objects.create(user=captain, first_name='C', last_name='2')
        Profile.objects.create(user=applicant, first_name='A', last_name='2')
        captain.profile.add_team_membership(self.team_a, make_primary=True)
        applicant.profile.add_team_membership(self.team_b, make_primary=True)
        applicant.profile.team_requested = self.team_a
        applicant.profile.join_accept_as_primary = False
        applicant.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
        self.client.logout()
        self.assertTrue(self.client.login(username='mt_cap2', password='secret'))
        self.client.get('/confirm_user_joining_team/%d/' % applicant.pk)
        applicant.profile.refresh_from_db()
        self.assertIsNone(applicant.profile.team_requested)
        self.assertTrue(applicant.profile.join_accept_as_primary)
        self.assertEqual(applicant.profile.team_on_id, self.team_b.pk)
        self.assertTrue(
            ProfileTeamMembership.objects.filter(profile=applicant.profile, team=self.team_a).exists()
        )

