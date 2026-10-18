from datetime import timedelta
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.models import HTMLPage, Profile, Project, Registration, Game, Team


class ProjectScopedRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
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
        response = self.client.post(
            '/glowbyte/register/glowbyte_des_12/',
            {'next': '/glowbyte/'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/glowbyte/')
        self.assertTrue(
            Registration.objects.filter(game=self.game, team=self.team).exists()
        )

    def test_project_register_url_rejects_game_from_another_project(self):
        now = timezone.now()
        main_game = Game.objects.create(
            id='main_des_12',
            name='Main 12',
            outside_name='Main 12',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=True,
            requires_ticket=False,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            project_id='main',
        )

        response = self.client.post(
            '/glowbyte/register/main_des_12/',
            {'next': '/glowbyte/'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Registration.objects.filter(game=main_game, team=self.team).exists()
        )

    def test_started_game_still_accepts_registration(self):
        self.game.start_time = timezone.now() - timedelta(minutes=10)
        self.game.end_time = timezone.now() + timedelta(hours=1)
        self.game.save(update_fields=['start_time', 'end_time'])

        response = self.client.post('/register/%s/' % self.game.pk, {'next': '/'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Registration.objects.filter(game=self.game, team=self.team).exists())

    def test_repeated_root_registration_by_teammates_charges_one_ticket(self):
        self.game.requires_ticket = True
        self.game.save(update_fields=['requires_ticket'])
        self.team.tickets = 2
        self.team.save(update_fields=['tickets'])

        first = self.client.post('/register/%s/' % self.game.pk, {'next': '/'})
        self.client.force_login(self.other)
        repeated = self.client.post('/register/%s/' % self.game.pk, {'next': '/'})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(Registration.objects.filter(game=self.game, team=self.team).count(), 1)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 1)
        self.assertIn('уже зарегистрирована', str(list(get_messages(repeated.wsgi_request))[-1]))

    def test_closed_registration_redirects_with_message(self):
        for changes in (
            {'is_registrable': False},
            {'is_registrable': True, 'is_ready': False},
            {'is_ready': True, 'end_time': timezone.now() - timedelta(minutes=1)},
        ):
            with self.subTest(changes=changes):
                Game.objects.filter(pk=self.game.pk).update(**changes)
                response = self.client.post('/register/%s/' % self.game.pk, {'next': '/glowbyte/'})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], '/glowbyte/')
                self.assertIn('нельзя зарегистрироваться', str(list(get_messages(response.wsgi_request))[-1]))
                self.assertFalse(Registration.objects.filter(game=self.game, team=self.team).exists())

    def test_missing_ticket_redirects_without_registration(self):
        self.game.requires_ticket = True
        self.game.save(update_fields=['requires_ticket'])
        self.team.tickets = 0
        self.team.save(update_fields=['tickets'])

        response = self.client.post('/register/%s/' % self.game.pk, {'next': '/glowbyte/'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('купить билет', str(list(get_messages(response.wsgi_request))[-1]))
        self.assertFalse(Registration.objects.filter(game=self.game, team=self.team).exists())
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)

    def test_referral_validation_and_limit_do_not_return_500(self):
        self.game.requires_ticket = True
        self.game.save(update_fields=['requires_ticket'])
        referent = Team.objects.create(name='referrer', visible_name='Referrer')
        url = '/glowbyte/register/%s/' % self.game.pk
        payload = {'with_referent': referent.pk, 'next': '/glowbyte/'}

        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn('не зарегистрирована', str(list(get_messages(response.wsgi_request))[-1]))
        self.assertFalse(Registration.objects.filter(game=self.game, team=self.team).exists())

        Registration.objects.create(game=self.game, team=referent)
        # Historical referral usage is counted across games.
        for _ in range(3):
            Registration.objects.create(team=self.team, with_referent=referent)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn('больше чем 3 раза', str(list(get_messages(response.wsgi_request))[-1]))
        self.assertFalse(Registration.objects.filter(game=self.game, team=self.team).exists())

    def test_valid_referral_does_not_charge_ticket(self):
        self.game.requires_ticket = True
        self.game.save(update_fields=['requires_ticket'])
        referent = Team.objects.create(name='referrer', visible_name='Referrer')
        Registration.objects.create(game=self.game, team=referent)
        self.team.tickets = 0
        self.team.save(update_fields=['tickets'])

        response = self.client.post(
            '/register/%s/' % self.game.pk,
            {'with_referent': referent.pk, 'next': '/'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Registration.objects.get(game=self.game, team=self.team).with_referent, referent)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)

    def test_failed_registration_rolls_back_ticket_charge(self):
        self.game.requires_ticket = True
        self.game.save(update_fields=['requires_ticket'])
        self.team.tickets = 2
        self.team.save(update_fields=['tickets'])

        with patch('games.views.registration.Registration.objects.create', side_effect=RuntimeError('save failed')):
            with self.assertRaisesMessage(RuntimeError, 'save failed'):
                self.client.post('/register/%s/' % self.game.pk, {'next': '/'})

        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 2)
        self.assertFalse(Registration.objects.filter(game=self.game, team=self.team).exists())

    def test_project_team_moderation_urls_accept_project_id(self):
        response = self.client.post(
            '/glowbyte/kick_out_user/%d/' % self.other.pk,
            {'next': '/glowbyte/team/'},
            HTTP_REFERER='/glowbyte/team/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/glowbyte/team/')
