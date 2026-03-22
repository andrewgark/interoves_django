from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import Profile, ProfileTeamMembership, Project, Team


class JoinByPasswordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.team = Team.objects.create(
            name='join_pw_team_slug',
            visible_name='Bad Treap',
            join_password='a1b2c3d4',
        )
        cls.user = User.objects.create_user('join_pw_user', 'join_pw_user@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='J', last_name='P')

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='join_pw_user', password='secret'))

    def test_join_accepts_uppercase_code(self):
        url = reverse('new_team_join_by_password')
        resp = self.client.post(
            url,
            {'name': 'Bad Treap', 'password': 'A1B2C3D4'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.team.pk)
        self.assertTrue(
            ProfileTeamMembership.objects.filter(profile=self.user.profile, team=self.team).exists()
        )

    def test_join_by_visible_name_and_mixed_case_password(self):
        url = reverse('new_team_join_by_password')
        resp = self.client.post(
            url,
            {'name': 'bad treap', 'password': 'A1b2C3d4'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.team.pk)

    def test_wrong_password_rejected(self):
        url = reverse('new_team_join_by_password')
        resp = self.client.post(
            url,
            {'name': 'join_pw_team_slug', 'password': '00000000'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertIsNone(self.user.profile.team_on)
