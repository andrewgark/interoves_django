"""Pay page gating + «Купить билеты» CTA on main hub only."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import Game, HTMLPage, Profile, Project, Team


def _ensure_login_modal_deps():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
    for provider, name in (('google', 'Google'), ('vk', 'VK')):
        app, created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        if created:
            app.sites.add(site)


@override_settings(LANGUAGE_CODE='ru-ru')
class PayPageGatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()
        cls.user = User.objects.create_user('pay_user', 'pay_user@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='P', last_name='U')
        cls.team = Team.objects.create(
            name='pay_team',
            visible_name='Pay Team',
            project_id='main',
            tickets=3,
            ticket_price=2000,
        )

    def setUp(self):
        self.client = Client()

    def test_anonymous_sees_login_prompt_not_payment_form(self):
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Чтобы купить билет для команды, сначала войдите', body)
        self.assertIn('data-login-open', body)
        self.assertNotIn('new-pay-ticket-form', body)
        self.assertNotIn('Создать команду', body)

    def test_authenticated_without_team_sees_create_or_join(self):
        self.assertTrue(self.client.login(username='pay_user', password='secret'))
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('создайте новую или вступите в существующую', body)
        self.assertIn('/team/create/', body)
        self.assertIn('/team/join/', body)
        self.assertIn('Создать команду', body)
        self.assertIn('Вступить в команду', body)
        self.assertNotIn('new-pay-ticket-form', body)
        self.assertNotIn('data-login-open', body)

    def test_authenticated_with_team_sees_payment_form(self):
        self.user.profile.add_team_membership(self.team, make_primary=True)
        self.assertTrue(self.client.login(username='pay_user', password='secret'))
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('new-pay-ticket-form', body)
        self.assertIn('Оплатить российской картой', body)
        self.assertIn('Оплатить криптой', body)
        self.assertIn('new-pay-widget-host', body)
        self.assertIn('Pay Team', body)
        self.assertIn('Билетов сейчас: <strong>3</strong>', body)
        self.assertNotIn('Создать команду', body)
        self.assertNotIn('data-login-open', body)


@override_settings(LANGUAGE_CODE='ru-ru')
class HubPayCtaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        now = timezone.now()
        Game.objects.create(
            id='des_hub_pay',
            name='Десяточка hub pay',
            outside_name='Десяточка hub pay',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
            project_id='main',
        )
        Game.objects.create(
            id='gb_hub_pay',
            name='Glowbyte game',
            outside_name='Glowbyte game',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
            project_id='glowbyte',
        )

    def setUp(self):
        self.client = Client()

    def test_main_hub_shows_buy_tickets_in_desyatochki(self):
        resp = self.client.get(reverse('new_hub'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Купить билеты', body)
        self.assertIn('href="/pay/"', body)
        self.assertTrue(resp.context['show_desyatochki_pay_cta'])

    def test_glowbyte_hub_does_not_show_buy_tickets(self):
        resp = self.client.get(reverse('project_hub', kwargs={'project_id': 'glowbyte'}))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn('Купить билеты', body)
        self.assertFalse(resp.context.get('show_desyatochki_pay_cta'))
