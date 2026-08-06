import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import HTMLPage, Profile, Project, Team, TicketRequest
from games.ticket_service import STUCK_TICKET_REQUEST_MINUTES, stuck_pending_ticket_count
from games.tribute_util import compute_webhook_signature, verify_webhook_signature


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


class TributeSignatureTests(TestCase):
    def test_verify_valid_signature(self):
        body = b'{"name":"shop_order","payload":{"uuid":"abc"}}'
        secret = 'tribute-api-key'
        sig = compute_webhook_signature(body, secret)
        self.assertTrue(verify_webhook_signature(body, sig, api_key=secret))

    def test_verify_rejects_bad_signature(self):
        body = b'{"name":"shop_order"}'
        self.assertFalse(verify_webhook_signature(body, 'deadbeef', api_key='secret'))
        self.assertFalse(verify_webhook_signature(body, None, api_key='secret'))


class TributeWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='tr_ipn_team', visible_name='Tribute Team', tickets=0)

    def setUp(self):
        self.http = Client()
        self.api_key = 'test-tribute-key'

    def _post_webhook(self, payload, *, event='shop_order', sig=None, api_key=None):
        body_obj = {
            'name': event,
            'created_at': '2025-03-20T01:15:58.332Z',
            'sent_at': '2025-03-20T01:15:58.542Z',
            'payload': payload,
        }
        body = json.dumps(body_obj).encode('utf-8')
        if sig is None:
            sig = compute_webhook_signature(body, api_key or self.api_key)
        return self.http.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=sig,
        )

    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_shop_order_paid_credits_tickets(self, _verify_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Pending',
            tribute_id='550e8400-e29b-41d4-a716-446655440000',
        )
        response = self._post_webhook({
            'uuid': '550e8400-e29b-41d4-a716-446655440000',
            'shopId': 1,
            'amount': 400000,
            'currency': 'rub',
            'fee': 1000,
            'status': 'paid',
            'isRecurrent': False,
            'customerId': f'ticket:{ticket.pk}',
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 2)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_shop_order_paid_is_idempotent(self, _verify_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Accepted',
            tribute_id='550e8400-e29b-41d4-a716-446655440001',
        )
        self.team.tickets = 2
        self.team.save(update_fields=['tickets'])

        response = self._post_webhook({
            'uuid': '550e8400-e29b-41d4-a716-446655440001',
            'shopId': 1,
            'amount': 400000,
            'currency': 'rub',
            'fee': 1000,
            'status': 'paid',
            'isRecurrent': False,
        })
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 2)

    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_payment_failed_rejects_pending(self, _verify_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
            tribute_id='550e8400-e29b-41d4-a716-446655440002',
        )
        response = self._post_webhook(
            {
                'uuid': '550e8400-e29b-41d4-a716-446655440002',
                'shopId': 1,
                'amount': 200000,
                'currency': 'rub',
                'starsAmount': 0,
                'onlyStars': False,
            },
            event='shop_order_payment_failed',
        )
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Rejected')
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_lookup_by_customer_id_fallback(self, _verify_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
        )
        response = self._post_webhook({
            'uuid': '550e8400-e29b-41d4-a716-446655440099',
            'shopId': 1,
            'amount': 200000,
            'currency': 'rub',
            'fee': 1000,
            'status': 'paid',
            'isRecurrent': False,
            'customerId': f'ticket:{ticket.pk}',
        })
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(ticket.tribute_id, '550e8400-e29b-41d4-a716-446655440099')

    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_payment_received_ignored(self, _verify_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
            tribute_id='550e8400-e29b-41d4-a716-446655440003',
        )
        response = self._post_webhook(
            {
                'uuid': '550e8400-e29b-41d4-a716-446655440003',
                'shopId': 1,
                'amount': 200000,
                'currency': 'rub',
                'starsAmount': 0,
                'onlyStars': False,
            },
            event='shop_order_payment_received',
        )
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Pending')

    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=False)
    def test_bad_signature_returns_400(self, _verify_mock):
        response = self._post_webhook(
            {'uuid': 'x', 'status': 'paid'},
            sig='bad',
        )
        self.assertEqual(response.status_code, 400)

    @patch('games.views.ticket.verify_tribute_webhook_signature', return_value=True)
    def test_unknown_order_returns_200(self, _verify_mock):
        with self.assertLogs('games.views.ticket', level='WARNING') as logs:
            response = self._post_webhook({
                'uuid': '00000000-0000-0000-0000-000000000099',
                'shopId': 1,
                'amount': 100,
                'currency': 'rub',
                'fee': 1,
                'status': 'paid',
                'isRecurrent': False,
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('ticket request not found' in line for line in logs.output))


class TributeCreatePaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('tr_pay_user', 'tr@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='T', last_name='R')
        cls.team = Team.objects.create(
            name='tr_pay_team',
            visible_name='Tribute Pay Team',
            project_id='main',
            tickets=0,
            ticket_price=2000,
        )
        cls.user.profile.add_team_membership(cls.team, make_primary=True)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='tr_pay_user', password='secret'))

    @patch('games.views.new_ui.tribute_create_shop_order')
    def test_create_tribute_payment_returns_url(self, create_mock):
        create_mock.return_value = {
            'uuid': '550e8400-e29b-41d4-a716-446655440010',
            'paymentUrl': 'https://web.tribute.tg/pay/550e8400-e29b-41d4-a716-446655440010',
        }
        response = self.client.post(
            reverse('new_create_tribute_ticket_payment'),
            {'tickets': '2'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['tribute_id'], '550e8400-e29b-41d4-a716-446655440010')
        self.assertIn('web.tribute.tg', data['payment_url'])
        ticket = TicketRequest.objects.filter(team=self.team).latest('id')
        self.assertEqual(ticket.status, 'Pending')
        self.assertEqual(ticket.tickets, 2)
        self.assertEqual(ticket.money, 4000)
        self.assertEqual(ticket.tribute_id, '550e8400-e29b-41d4-a716-446655440010')
        self.assertEqual(data['ticket_request_id'], ticket.id)
        self.assertIn('/pay/ticket-status/', data['status_url'])
        create_mock.assert_called_once()
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs['amount_rub'], 4000)
        self.assertEqual(kwargs['customer_id'], f'ticket:{ticket.id}')

    @patch(
        'games.views.new_ui.tribute_create_shop_order',
        side_effect=RuntimeError('Missing Tribute API key: ...'),
    )
    def test_create_tribute_payment_missing_credentials(self, _create_mock):
        response = self.client.post(
            reverse('new_create_tribute_ticket_payment'),
            {'tickets': '1'},
        )
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data['reason'], 'tribute_config')


class TributeStuckTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='tr_stuck_team', visible_name='Stuck', tickets=0)

    def test_stuck_counts_tribute_id(self):
        stuck = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
            tribute_id='550e8400-e29b-41d4-a716-446655440020',
        )
        TicketRequest.objects.filter(pk=stuck.pk).update(
            time=timezone.now() - timedelta(minutes=STUCK_TICKET_REQUEST_MINUTES + 5),
        )
        self.assertEqual(stuck_pending_ticket_count(), 1)
