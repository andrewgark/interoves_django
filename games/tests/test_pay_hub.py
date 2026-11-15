"""Payments hub at /pay/: links every money flow; ticket checkout lives at /pay/tickets/."""
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from games.models import Profile, Team
from games.tests.test_pay_page import _ensure_login_modal_deps


@override_settings(LANGUAGE_CODE='ru-ru')
class PayHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_login_modal_deps()
        cls.user = User.objects.create_user('hub_user', 'hub_user@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='H', last_name='U')
        cls.team = Team.objects.create(
            name='hub_team', visible_name='Hub Team', project_id='main', tickets=2, ticket_price=2000,
        )

    def setUp(self):
        self.client = Client()

    def test_urls(self):
        self.assertEqual(reverse('new_pay_hub'), '/pay/')
        self.assertEqual(reverse('new_pay'), '/pay/tickets/')

    def test_anonymous_sees_all_money_flows(self):
        resp = self.client.get('/pay/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        for href in ('/pay/tickets/', '/subscription/', '/donate/', '/subscription/?gift=1#gift', '/order-game/', '/refunds/'):
            self.assertIn('href="{}"'.format(href), body)
        self.assertNotIn('new-pay-hub__status', body)

    def test_nav_wallet_is_shown_and_current_on_hub(self):
        body = self.client.get('/pay/').content.decode()
        self.assertIn('new-nav__quick--pay', body)
        self.assertRegex(body, r'new-nav__quick--pay"\s+href="/pay/"[^>]*aria-current="page"')

    def test_member_sees_team_tickets(self):
        self.user.profile.add_team_membership(self.team, make_primary=True)
        self.client.force_login(self.user)
        body = self.client.get('/pay/').content.decode()
        self.assertIn('2 билета', body)

    def test_legacy_payment_return_goes_to_ticket_checkout(self):
        resp = self.client.get('/pay/?payment=return')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/pay/tickets/?payment=return')
        resp = self.client.get('/pay/?telegram=linked')
        self.assertEqual(resp['Location'], '/pay/tickets/?telegram=linked')

    def test_my_payments_links_only_when_there_is_history(self):
        from games.models import TicketRequest
        self.user.profile.add_team_membership(self.team, make_primary=True)
        self.client.force_login(self.user)
        self.assertNotIn('Мои платежи', self.client.get('/pay/').content.decode())
        TicketRequest.objects.create(team=self.team, tickets=1, money=2000)
        body = self.client.get('/pay/').content.decode()
        self.assertIn('Мои платежи', body)
        self.assertIn('href="/pay/tickets/#orders"', body)
