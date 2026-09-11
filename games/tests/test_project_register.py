from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.models import HTMLPage, Profile, Project, Registration, Game, Team


class ProjectScopedRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

        now = timezone.now()
        cls.game = Game.objects.create(
            id='glowbyte_des_12',
            name='Glowbyte 12',
            outside_name='Glowbyte 12',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=True,
            requires_ticket=False,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            project_id='glowbyte',
        )
        cls.team = Team.objects.create(
            name='gb_reg_team',
            visible_name='Glowbyte Team',
            project_id='glowbyte',
        )
        cls.user = User.objects.create_user('gb_reg_user', 'gb_reg_user@example.com', 'secret')
        Profile.objects.create(
            user=cls.user,
            first_name='G',
            last_name='B',
            team_on=cls.team,
        )
        cls.other = User.objects.create_user('gb_other_user', 'gb_other_user@example.com', 'secret')
        Profile.objects.create(
            user=cls.other,
            first_name='O',
            last_name='T',
            team_on=cls.team,
        )

    def setUp(self):
        self.assertTrue(self.client.login(username='gb_reg_user', password='secret'))

    def test_project_register_url_accepts_project_id_and_creates_registration(self):
        response = self.client.get(
            '/glowbyte/register/glowbyte_des_12/?next=/glowbyte/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/glowbyte/')
        self.assertTrue(
            Registration.objects.filter(game=self.game, team=self.team).exists()
        )

    def test_project_team_moderation_urls_accept_project_id(self):
        response = self.client.get(
            '/glowbyte/kick_out_user/%d/' % self.other.pk,
            HTTP_REFERER='/glowbyte/team/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/glowbyte/team/')
