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
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()

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
        self.assertNotIn('Чтобы купить билет для команды, сначала войдите', body)

    def test_authenticated_with_team_sees_payment_form(self):
        self.user.profile.add_team_membership(self.team, make_primary=True)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.team.pk)
        self.assertTrue(self.client.login(username='pay_user', password='secret'))
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('new-pay-ticket-form', body)
        self.assertIn('Оплатить российской картой', body)
        self.assertIn('Оплатить криптой', body)
        self.assertNotIn('Оплатить иностранной картой', body)
        self.assertIn('new-pay-widget-host', body)
        self.assertIn('Pay Team', body)
        self.assertIn('Билетов сейчас:', body)
        self.assertIn('id="new-pay-team-tickets">3</strong>', body)
        self.assertNotIn('Создать команду', body)


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
        self.assertIn('new-btn--yellow', body)
        self.assertTrue(resp.context['show_desyatochki_pay_cta'])

    def test_main_hub_shows_donate_in_interesting(self):
        resp = self.client.get(reverse('new_hub'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Наши интересности', body)
        self.assertIn('Nutrimatic', body)
        self.assertIn('href="/nutrimatic-ru/"', body)
        self.assertIn('Буклеты к Евровидению', body)
        self.assertIn('href="/eurovision_booklet/"', body)
        self.assertIn('VPN от наших друзей', body)
        self.assertIn('Задонатить', body)
        self.assertIn('href="/donate/"', body)
        self.assertTrue(resp.context['show_donate_cta'])
        # Чат участников продублирован в блок десяточек только на главной.
        self.assertEqual(
            resp.context['desyatochki_participants_chat_url'],
            'https://t.me/+rhsbkEuU4-ExOWEy',
        )
        self.assertIn('Чат участников', body)

    def test_glowbyte_hub_has_no_interesting_or_desyatochki_chat(self):
        resp = self.client.get(reverse('project_hub', kwargs={'project_id': 'glowbyte'}))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn('Наши интересности', body)
        self.assertFalse(resp.context.get('show_donate_cta'))
        self.assertIsNone(resp.context.get('desyatochki_participants_chat_url'))

    def test_glowbyte_hub_does_not_show_buy_tickets(self):
        resp = self.client.get(reverse('project_hub', kwargs={'project_id': 'glowbyte'}))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn('Купить билеты', body)
        self.assertFalse(resp.context.get('show_desyatochki_pay_cta'))


@override_settings(LANGUAGE_CODE='ru-ru')
class DonatePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def setUp(self):
        self.client = Client()

    def test_donate_page_is_public_and_shows_both_methods(self):
        resp = self.client.get(reverse('donate'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Задонатить с российской карты', body)
        self.assertIn('Задонатить крипту', body)
        self.assertIn('4100116763559349', body)
        self.assertIn('new-donate-amount', body)
        self.assertIn('donate/create-crypto-payment', body)
        self.assertIn('подтверждение транзакции может занимать десятки минут', body)
        self.assertNotIn('nowpayments.io/embeds/donation-widget', body)

    def test_donate_page_shows_recent_donations_from_session(self):
        from games.models import Donation

        donation = Donation.objects.create(amount_rub=120, status='Pending')
        session = self.client.session
        session['donate_recent_ids'] = [donation.id]
        session.save()
        resp = self.client.get(reverse('donate'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Последние донаты', body)
        self.assertIn('120 ₽', body)
        self.assertIn('Pending', body)
        self.assertIn('new-donate-recent__kind', body)
        self.assertIn('data-donation-id="{}"'.format(donation.id), body)

    def test_donate_is_not_captured_as_project_hub(self):
        resp = self.client.get('/donate/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new/donate.html')
