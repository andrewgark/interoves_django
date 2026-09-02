"""Pay page gating + «Купить билеты» CTA on main hub only."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import Game, HTMLPage, Profile, Project, Team, TicketRequest
from games.payment_routes import CRYPTO, INTERNATIONAL_CARD, RUSSIAN_CARD, amount_for, route_for, unit_price_for


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

    def test_anonymous_sees_public_checkout_and_login_gate(self):
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Войти и продолжить', body)
        self.assertIn('data-login-open', body)
        self.assertIn('new-pay-ticket-form', body)
        self.assertIn('Российская карта', body)
        self.assertIn('Visa / Mastercard', body)
        self.assertIn('Криптовалюта', body)
        self.assertIn('NOWPayments', body)
        self.assertIn('10 000 ֏', body)
        self.assertIn('2 500 ֏', body)
        self.assertIn('24 €', body)
        self.assertIn('6 €', body)
        self.assertIn('2 000 ₽', body)
        self.assertIn('500 ₽', body)
        self.assertIn('Сколько билетов?', body)
        self.assertIn('data-pay-qty-minus', body)
        self.assertIn('data-pay-qty-plus', body)
        self.assertIn('Один билет — одна игра для команды', body)
        self.assertIn('Льготная цена для школьных и студенческих команд', body)
        self.assertIn('Попросить льготу в Telegram', body)
        self.assertIn('href="https://t.me/andrewgark"', body)
        self.assertIn('id="new-pay-total-breakdown"', body)
        self.assertIn('Андрей Гаркавый, плательщик НПД, РФ', body)
        self.assertIn('/terms/russia/', body)
        self.assertIn('/terms/crypto/', body)
        self.assertIn('/refunds/', body)
        self.assertIn('/privacy/', body)
        self.assertIn('/contacts/', body)
        self.assertEqual(body.count('<footer class="new-legal-footer">'), 1)
        self.assertNotIn('<footer class="new-site-footer">', body)
        self.assertNotIn('Чтобы купить билет', body)
        self.assertNotIn('/terms-of-use/', body)
        self.assertNotIn('/ticket-agreement/', body)
        self.assertNotIn('/privacy-policy/', body)
        self.assertNotIn('TODO_', body)
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertNotIn('Создать команду', body)

    def test_authenticated_without_team_sees_create_or_join(self):
        self.assertTrue(self.client.login(username='pay_user', password='secret'))
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Для покупки нужно создать команду или вступить в существующую', body)
        self.assertIn('/team/create/', body)
        self.assertIn('/team/join/', body)
        self.assertIn('Создать команду', body)
        self.assertIn('вступить в существующую команду', body)
        self.assertIn('new-pay-ticket-form', body)
        self.assertNotIn('Войти потребуется только перед созданием заказа', body)

    def test_authenticated_with_team_sees_payment_form(self):
        self.user.profile.add_team_membership(self.team, make_primary=True)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.team_on_id, self.team.pk)
        self.assertTrue(self.client.login(username='pay_user', password='secret'))
        resp = self.client.get(reverse('new_pay'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('new-pay-ticket-form', body)
        self.assertIn('Российская карта', body)
        self.assertIn('Visa / Mastercard', body)
        self.assertIn('Криптовалюта', body)
        self.assertIn('NOWPayments', body)
        self.assertIn('new-pay-widget-host', body)
        self.assertIn('Pay Team', body)
        self.assertIn('Билетов у команды сейчас:', body)
        self.assertIn('id="new-pay-team-tickets">3</strong>', body)
        self.assertNotIn('Создать команду', body)


class TicketPricingTests(TestCase):
    def test_standard_prices_are_independent(self):
        team = Team(ticket_price=2000, ticket_price_amd=10000)
        self.assertEqual(unit_price_for(team, RUSSIAN_CARD), 2000)
        self.assertEqual(unit_price_for(team, INTERNATIONAL_CARD), 10000)
        self.assertEqual(amount_for(team, INTERNATIONAL_CARD, 2), 20000)

    def test_discount_prices_are_independent(self):
        team = Team(ticket_price=500, ticket_price_amd=2500)
        self.assertEqual(amount_for(team, RUSSIAN_CARD, 3), 1500)
        self.assertEqual(amount_for(team, INTERNATIONAL_CARD, 3), 7500)
        self.assertEqual(amount_for(team, CRYPTO, 3), 1500)

    def test_payment_route_provider_merchant_currency_matrix(self):
        russian = route_for(RUSSIAN_CARD)
        self.assertEqual((russian.provider, russian.merchant, russian.currency), ('yookassa', 'ru_self_employed', 'RUB'))
        self.assertTrue(russian.enabled)
        self.assertEqual(russian.terms_url, '/terms/russia/')

        international = route_for(INTERNATIONAL_CARD)
        self.assertEqual((international.provider, international.merchant, international.currency), ('vpos', 'am_ie', 'AMD'))
        self.assertFalse(international.enabled)
        self.assertEqual(international.terms_url, '/terms/armenia/')

        crypto = route_for(CRYPTO)
        self.assertEqual((crypto.provider, crypto.merchant, crypto.currency), ('nowpayments', 'ru_self_employed', 'RUB'))
        self.assertTrue(crypto.enabled)
        self.assertEqual(crypto.terms_url, '/terms/crypto/')


@override_settings(LANGUAGE_CODE='ru-ru')
class TicketPaymentCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()
        cls.user = User.objects.create_user('pay_create', 'create@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='Pay', last_name='Create')
        cls.team = Team.objects.create(
            name='pay_create_team',
            visible_name='Create Team',
            project_id='main',
            ticket_price=500,
            ticket_price_amd=2500,
        )
        cls.user.profile.add_team_membership(cls.team, make_primary=True)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='pay_create', password='secret'))

    @patch('games.views.new_ui.configure_yookassa_from_env')
    @patch('games.views.new_ui.Payment.create')
    def test_yookassa_uses_server_price_and_stores_route(self, create_mock, _configure_mock):
        create_mock.return_value = {
            'id': 'pay-route-1',
            'confirmation': {'confirmation_token': 'token-1'},
        }
        self.client.cookies['_ym_uid'] = '1234567890123456789'
        response = self.client.post(
            reverse('new_create_ticket_payment'),
            {'tickets': '2', 'money': '1', 'currency': 'AMD'},
        )
        self.assertEqual(response.status_code, 200)
        ticket = TicketRequest.objects.latest('id')
        self.assertEqual(ticket.created_by, self.user)
        self.assertEqual(ticket.metrika_client_id, '1234567890123456789')
        self.assertEqual(ticket.money, 1000)
        self.assertEqual(ticket.currency, 'RUB')
        self.assertEqual(ticket.payment_provider, 'yookassa')
        self.assertEqual(ticket.merchant, 'ru_self_employed')
        event = response.json()['analytics_events'][0]
        self.assertEqual(event['goal'], 'ticket_checkout')
        self.assertIn('ack', event)
        ack = self.client.post(event['ack']['url'], {'token': event['ack']['token']})
        self.assertEqual(ack.status_code, 200)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.checkout_goal_acked_at)
        payload = create_mock.call_args.args[0]
        self.assertEqual(payload['amount'], {'value': '1000.00', 'currency': 'RUB'})

    def test_status_exposes_payment_route(self):
        ticket = TicketRequest.objects.create(
            team=self.team,
            money=500,
            tickets=1,
            currency='RUB',
            payment_provider='nowpayments',
            merchant='ru_self_employed',
        )
        response = self.client.get(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['currency'], 'RUB')
        self.assertEqual(response.json()['payment_provider'], 'nowpayments')


@override_settings(LANGUAGE_CODE='ru-ru')
class LegalPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()

    def test_all_legal_pages_are_public_without_checkout_footer(self):
        for name in ('sellers', 'terms', 'terms_russia', 'terms_armenia', 'terms_crypto', 'refunds', 'privacy', 'contacts'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertNotIn('TODO_', body)
                self.assertNotIn('<footer class="new-legal-footer">', body)
                self.assertIn('no-store', response['Cache-Control'])

    def test_current_terms_have_route_specific_copy(self):
        russian = self.client.get(reverse('terms_russia')).content.decode()
        self.assertIn('ЮKassa', russian)
        self.assertIn('2 000 RUB', russian)
        self.assertIn('500 RUB', russian)
        self.assertIn('Применимое право и обращения', russian)
        self.assertIn('документально подтвержденных фактически понесенных', russian)
        self.assertNotIn('NOWPayments', russian)

        armenian = self.client.get(reverse('terms_armenia')).content.decode()
        self.assertIn('10 000 ֏', armenian)
        self.assertIn('2 500 ֏', armenian)
        self.assertIn('Andrei Garkavyi IE', armenian)
        self.assertIn('право Республики Армения', armenian)
        self.assertIn('Международные карты', armenian)
        self.assertNotIn('TODO_', armenian)

        crypto = self.client.get(reverse('terms_crypto')).content.decode()
        self.assertIn('NOWPayments', crypto)
        self.assertIn('Эти условия применяются только при выборе оплаты криптовалютой', crypto)
        self.assertIn('ru_self_employed', route_for(CRYPTO).merchant)
        self.assertIn('документально подтвержденных фактически понесенных', crypto)
        self.assertNotIn('ЮKassa обрабатывает', crypto)

    def test_refunds_and_privacy_have_closed_review_copy(self):
        refunds = self.client.get(reverse('refunds')).content.decode()
        self.assertIn('Покупатель может отказаться от дальнейшего участия и после начала мероприятия', refunds)
        self.assertIn('Отдельный штраф за отказ', refunds)
        self.assertIn('Возвраты по оплате криптовалютой не автоматизированы', refunds)

        privacy = self.client.get(reverse('privacy')).content.decode()
        self.assertIn('Кто отвечает за обработку данных', privacy)
        self.assertIn('Основания обработки', privacy)
        self.assertIn('не менее 5 лет', privacy)
        self.assertIn('в течение 30 дней', privacy)
        self.assertIn('до 3 лет', privacy)
        self.assertIn('не более 12 месяцев', privacy)
        self.assertIn('NOWPayments используется только если вы сами выбираете оплату криптовалютой', privacy)
        self.assertIn('Трансграничная передача', privacy)

    def test_legacy_documents_redirect_permanently(self):
        expected = {
            '/privacy-policy/': '/privacy/',
            '/terms-of-use/': '/terms/',
            '/ticket-agreement/': '/terms/russia/',
        }
        for old_url, new_url in expected.items():
            with self.subTest(old_url=old_url):
                response = self.client.get(old_url)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], new_url)

    def test_legacy_document_redirects_keep_query_strings(self):
        response = self.client.get('/terms-of-use/?source=legacy')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/terms/?source=legacy')

    def test_legacy_document_redirects_without_trailing_slash(self):
        expected = {
            '/privacy-policy': '/privacy/',
            '/terms-of-use': '/terms/',
            '/ticket-agreement': '/terms/russia/',
        }
        for old_url, new_url in expected.items():
            with self.subTest(old_url=old_url):
                response = self.client.get(old_url)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], new_url)

    def test_old_terms_copy_is_not_public(self):
        response = self.client.get('/terms-of-use/', follow=True)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('Last updated February 08, 2021', body)
        self.assertNotIn('TODO_', body)


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

    def test_main_hub_shows_shared_site_footer(self):
        resp = self.client.get(reverse('new_hub'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertEqual(body.count('<footer class="new-site-footer">'), 1)
        self.assertIn('href="/about/"', body)
        self.assertIn('href="https://pinely.com/"', body)
        self.assertIn('img/sponsors/pinely.png', body)
        self.assertIn('Спонсор', body)
        self.assertIn('>Pinely</span>', body)
        self.assertIn('href="https://t.me/interoves"', body)
        self.assertIn('href="https://x.com/interoves"', body)
        self.assertIn('href="https://www.instagram.com/interoveslocumpraesta/"', body)

    def test_about_page_is_public_and_uses_shared_footer(self):
        resp = self.client.get(reverse('about'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertContains(resp, '<h1 class="new-heading new-heading--page">О нас</h1>', html=True)
        self.assertEqual(body.count('<h1'), 1)
        self.assertIn('<title>О нас · Interoves</title>', body)
        self.assertIn('name="description" content="Inter Oves — клуб сложных головоломок, где интернет разрешён.', body)
        self.assertIn('<link rel="canonical" href="https://interoves.com/about/">', body)
        self.assertIn('Inter Oves Locum Praesta — клуб сложных головоломок.', body)
        self.assertIn('href="https://t.me/andrewgark"', body)
        self.assertIn('href="https://vk.ru/interoveslocumpraesta"', body)
        self.assertIn('href="https://m.vk.ru/ag.expromt"', body)
        self.assertIn('href="/games/"', body)
        self.assertIn('href="/ladder/"', body)
        self.assertIn('href="/alphabetty/"', body)
        self.assertIn('href="/salad/"', body)
        self.assertIn('href="/nutrimatic-ru/"', body)
        self.assertIn('href="/eurovision_booklet/2026/"', body)
        self.assertIn('href="/vpn/"', body)
        self.assertIn('href="/order-game/"', body)
        self.assertIn('alt="Логотип Pinely"', body)
        self.assertEqual(body.count('<footer class="new-site-footer">'), 1)

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

    def test_glowbyte_pages_use_client_logo_instead_of_pinely_sponsor(self):
        urls = (
            reverse('project_hub', kwargs={'project_id': 'glowbyte'}),
            reverse('project_folder_games', kwargs={'project_id': 'glowbyte'}),
            reverse(
                'project_main_game',
                kwargs={'project_id': 'glowbyte', 'game_id': 'gb_hub_pay'},
            ),
        )

        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                body = resp.content.decode()
                self.assertEqual(body.count('<footer class="new-site-footer">'), 1)
                self.assertIn('src="/media/GlowByte_Logo.png"', body)
                self.assertIn('alt="Логотип GlowByte"', body)
                self.assertNotIn('href="https://pinely.com/"', body)
                self.assertNotIn('img/sponsors/pinely.png', body)
                self.assertNotIn('>Pinely</span>', body)
                self.assertNotIn('new-site-footer__sponsor-caption', body)


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
        self.assertIn('Оплата может идти несколько минут', body)
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
        self.assertIn('Ожидает', body)
        self.assertIn('new-donate-recent__kind', body)
        self.assertIn('data-donation-id="{}"'.format(donation.id), body)

    def test_donate_is_not_captured_as_project_hub(self):
        resp = self.client.get('/donate/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'new/donate.html')
