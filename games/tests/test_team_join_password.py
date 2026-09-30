from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import Profile, ProfileTeamMembership, Project, Team


class JoinByPasswordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        cls.team = Team.objects.create(
            name='join_pw_team_slug',
            visible_name='Bad Treap',
            join_password='a1b2c3d4',
        )
        cls.glowbyte_team = Team.objects.create(
            name='glowbyte_join_pw_team',
            visible_name='Glowbyte Team',
            project_id='glowbyte',
            join_password='e5f6a7b8',
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

    def test_visible_name_wins_over_another_team_stable_name(self):
        Team.objects.create(
            name='Bad Treap',
            visible_name='Another Team',
            join_password='00000000',
        )
        resp = self.client.get(
            reverse('new_team_info'),
            {'name': 'Bad Treap'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['visible_name'], 'Bad Treap')

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

    def test_join_accepts_glowbyte_team(self):
        url = reverse('new_team_join_by_password')
        resp = self.client.post(
            url,
            {'name': 'Glowbyte Team', 'password': 'E5F6A7B8'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.glowbyte_team.pk)

    def test_glowbyte_team_can_become_primary(self):
        self.user.profile.add_team_membership(self.glowbyte_team, make_primary=False)
        resp = self.client.post(
            reverse('new_team_set_primary'),
            {'team': self.glowbyte_team.pk},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.glowbyte_team.pk)

    def test_scoped_create_uses_project_from_url(self):
        resp = self.client.post(
            reverse('project_team_create', kwargs={'project_id': 'glowbyte'}),
            {'name': 'Created on Glowbyte', 'make_primary': '0'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Team.objects.get(name='Created on Glowbyte').project_id,
            'glowbyte',
        )

    def test_name_check_is_global(self):
        resp = self.client.get(
            reverse('new_team_name_check'),
            {'name': 'Glowbyte Team'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['available'])

    def test_rename_rejects_existing_display_name(self):
        self.user.profile.add_team_membership(self.team, make_primary=True)
        resp = self.client.post(
            reverse('new_team_rename'),
            {'visible_name': 'Glowbyte Team'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.visible_name, 'Bad Treap')
