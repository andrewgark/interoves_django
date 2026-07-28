import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import HTMLPage, Profile, Project, Team, TicketRequest
from games.nowpayments_util import compute_ipn_signature, verify_ipn_signature
from games.ticket_service import STUCK_TICKET_REQUEST_MINUTES, stuck_pending_ticket_count


def _ensure_reference_rows():
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


class NowPaymentsSignatureTests(TestCase):
    def test_verify_valid_signature(self):
        payload = {
            'payment_id': 123,
            'payment_status': 'finished',
            'order_id': '42',
            'fee': {'currency': 'btc', 'depositFee': 0.1},
        }
        secret = 'ipn-secret'
        sig = compute_ipn_signature(payload, secret)
        self.assertTrue(verify_ipn_signature(payload, sig, ipn_secret=secret))

    def test_verify_rejects_bad_signature(self):
        payload = {'payment_status': 'finished', 'order_id': '1'}
        self.assertFalse(verify_ipn_signature(payload, 'deadbeef', ipn_secret='secret'))
        self.assertFalse(verify_ipn_signature(payload, None, ipn_secret='secret'))


class NowPaymentsIpnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='np_ipn_team', visible_name='NP Team', tickets=0)

    def setUp(self):
        self.http = Client()
        self.secret = 'test-ipn-secret'

    def _post_ipn(self, payload, *, sig=None, secret=None):
        body = json.dumps(payload)
        if sig is None:
            sig = compute_ipn_signature(payload, secret or self.secret)
        return self.http.post(
            '/nowpayments/ipn/',
            data=body,
            content_type='application/json',
            HTTP_X_NOWPAYMENTS_SIG=sig,
        )

    @override_settings()
    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.verify_ipn_signature', return_value=True)
    def test_finished_credits_tickets(self, _verify_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Pending',
            nowpayments_id='inv-1',
        )
        response = self._post_ipn({
            'payment_id': 999,
            'invoice_id': 'inv-1',
            'payment_status': 'finished',
            'order_id': str(ticket.pk),
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 2)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.verify_ipn_signature', return_value=True)
    def test_finished_is_idempotent(self, _verify_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Accepted',
            nowpayments_id='inv-1',
        )
        self.team.tickets = 2
        self.team.save(update_fields=['tickets'])

        response = self._post_ipn({
            'payment_id': 999,
            'payment_status': 'finished',
            'order_id': str(ticket.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 2)

    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.verify_ipn_signature', return_value=True)
    def test_failed_rejects_pending(self, _verify_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
        )
        response = self._post_ipn({
            'payment_id': 1,
            'payment_status': 'failed',
            'order_id': str(ticket.pk),
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Rejected')
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.verify_ipn_signature', return_value=True)
    def test_partially_paid_leaves_pending(self, _verify_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
        )
        response = self._post_ipn({
            'payment_id': 1,
            'payment_status': 'partially_paid',
            'order_id': str(ticket.pk),
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Pending')

    @patch('games.views.ticket.verify_ipn_signature', return_value=False)
    def test_bad_signature_returns_400(self, _verify_mock):
        response = self._post_ipn(
            {'payment_status': 'finished', 'order_id': '1'},
            sig='bad',
        )
        self.assertEqual(response.status_code, 400)

    @patch('games.views.ticket.verify_ipn_signature', return_value=True)
    def test_unknown_order_returns_200(self, _verify_mock):
        with self.assertLogs('games.views.ticket', level='WARNING') as logs:
            response = self._post_ipn({
                'payment_status': 'finished',
                'order_id': '999999',
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('ticket request not found' in line for line in logs.output))


class NowPaymentsCreatePaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('np_pay_user', 'np@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='N', last_name='P')
        cls.team = Team.objects.create(
            name='np_pay_team',
            visible_name='NP Pay Team',
            project_id='main',
            tickets=0,
            ticket_price=2000,
        )
        cls.user.profile.add_team_membership(cls.team, make_primary=True)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='np_pay_user', password='secret'))

    @patch('games.views.new_ui.nowpayments_create_invoice')
    def test_create_crypto_payment_returns_embed(self, create_mock):
        create_mock.return_value = {
            'id': '6064785541',
            'invoice_url': 'https://nowpayments.io/payment/?iid=6064785541',
        }
        response = self.client.post(
            reverse('new_create_crypto_ticket_payment'),
            {'tickets': '2'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['invoice_id'], '6064785541')
        self.assertIn('iid=6064785541', data['embed_url'])
        ticket = TicketRequest.objects.filter(team=self.team).latest('id')
        self.assertEqual(ticket.status, 'Pending')
        self.assertEqual(ticket.tickets, 2)
        self.assertEqual(ticket.money, 4000)
        self.assertEqual(ticket.nowpayments_id, '6064785541')
        create_mock.assert_called_once()
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs['price_amount'], 4000)
        self.assertEqual(kwargs['price_currency'], 'rub')
        self.assertEqual(kwargs['order_id'], str(ticket.id))

    @patch(
        'games.views.new_ui.nowpayments_create_invoice',
        side_effect=RuntimeError('Missing NOWPayments API key: ...'),
    )
    def test_create_crypto_payment_missing_credentials(self, _create_mock):
        response = self.client.post(
            reverse('new_create_crypto_ticket_payment'),
            {'tickets': '1'},
        )
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data['reason'], 'nowpayments_config')


class NowPaymentsStuckTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='np_stuck_team', visible_name='Stuck', tickets=0)

    def test_stuck_counts_nowpayments_id(self):
        stuck = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
            nowpayments_id='inv-stuck',
        )
        TicketRequest.objects.filter(pk=stuck.pk).update(
            time=timezone.now() - timezone.timedelta(minutes=STUCK_TICKET_REQUEST_MINUTES + 5),
        )
        self.assertEqual(stuck_pending_ticket_count(), 1)
