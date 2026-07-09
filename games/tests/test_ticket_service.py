import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from games.models import HTMLPage, Project, Team, TicketRequest
from games.telegram.callbacks import _handle_ticket
from games.ticket_service import (
    STUCK_TICKET_REQUEST_MINUTES,
    accept_ticket_request,
    build_stuck_tickets_alert,
    reject_ticket_request,
    stuck_pending_ticket_count,
)

def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


class TicketServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='ticket_svc_team', visible_name='Ticket Team', tickets=1)

    def _pending(self, **kwargs):
        defaults = {
            'team': self.team,
            'tickets': 2,
            'money': 4000,
            'status': 'Pending',
        }
        defaults.update(kwargs)
        return TicketRequest.objects.create(**defaults)

    def test_accept_credits_team_tickets(self):
        ticket = self._pending()
        result = accept_ticket_request(ticket, yookassa_id='pay-1', source='test')

        self.assertTrue(result.changed)
        self.assertTrue(result.credited)
        self.assertEqual(result.tickets_credited, 2)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(ticket.yookassa_id, 'pay-1')
        self.assertEqual(self.team.tickets, 3)

    def test_accept_is_idempotent(self):
        ticket = self._pending()
        accept_ticket_request(ticket, source='test')
        result = accept_ticket_request(ticket, source='test')

        self.team.refresh_from_db()
        self.assertFalse(result.changed)
        self.assertTrue(result.already_accepted)
        self.assertEqual(self.team.tickets, 3)

    def test_accept_without_team_sets_accepted_without_credit(self):
        ticket = self._pending(team=None)
        result = accept_ticket_request(ticket, source='test')

        ticket.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertFalse(result.credited)
        self.assertTrue(result.no_team)
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 1)

    def test_reject_only_from_pending(self):
        ticket = self._pending()
        result = reject_ticket_request(ticket, source='test')
        ticket.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(ticket.status, 'Rejected')

        again = reject_ticket_request(ticket, source='test')
        self.assertFalse(again.changed)
        self.assertTrue(again.already_final)

    def test_stuck_pending_ticket_count(self):
        recent = self._pending(yookassa_id='recent-pay')
        stuck = self._pending(yookassa_id='stuck-pay')
        TicketRequest.objects.filter(pk=stuck.pk).update(
            time=timezone.now() - timezone.timedelta(minutes=STUCK_TICKET_REQUEST_MINUTES + 5),
        )
        TicketRequest.objects.filter(pk=recent.pk).update(
            time=timezone.now() - timezone.timedelta(minutes=5),
        )

        self.assertEqual(stuck_pending_ticket_count(), 1)

    def test_build_stuck_tickets_alert(self):
        self.assertIsNone(build_stuck_tickets_alert())
        stuck = self._pending(yookassa_id='stuck-pay')
        TicketRequest.objects.filter(pk=stuck.pk).update(
            time=timezone.now() - timezone.timedelta(minutes=STUCK_TICKET_REQUEST_MINUTES + 5),
        )
        alert = build_stuck_tickets_alert()
        self.assertIn('Зависшие', alert)
        self.assertIn('#{}'.format(stuck.pk), alert)


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
)
class TicketTelegramCallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='tg_ticket_team', visible_name='TG Ticket Team', tickets=0)

    @patch('games.telegram.callbacks.edit_message_reply_markup')
    @patch('games.telegram.callbacks.send_admin_message')
    @patch('games.telegram.callbacks.answer_callback_query')
    def test_telegram_accept_credits_tickets(self, answer_mock, _admin_mock, _edit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=3,
            money=6000,
            status='Pending',
        )
        _handle_ticket('accept', ticket.pk, 'cb-1', 12345, 99)

        self.team.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 3)
        answer_mock.assert_called_once()


class YooKassaWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.team = Team.objects.create(name='webhook_team', visible_name='Webhook Team', tickets=0)

    def setUp(self):
        self.http = Client()

    def _fake_payment(self, data):
        class FakePayment:
            def __iter__(self_inner):
                return iter(data.items())

        return FakePayment()

    def _payment_data(self, ticket_request_id, payment_id='pay-uuid-1'):
        return {
            'id': payment_id,
            'description': 'Билеты',
            'metadata': {
                'ticket_request_id': str(ticket_request_id),
                'kind': 'team_ticket',
            },
        }

    def _post_webhook(self, event, payment_id='pay-uuid-1'):
        body = {
            'event': event,
            'object': {'id': payment_id},
        }
        return self.http.post(
            '/yookassa/webhook/',
            data=json.dumps(body),
            content_type='application/json',
        )

    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.Payment.find_one')
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_succeeded_credits_tickets(self, _cfg_mock, find_one_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Pending',
            yookassa_id='pay-uuid-1',
        )
        find_one_mock.return_value = self._fake_payment(self._payment_data(ticket.pk))

        response = self._post_webhook('payment.succeeded')

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 2)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.Payment.find_one')
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_succeeded_is_idempotent(self, _cfg_mock, find_one_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=2,
            money=4000,
            status='Accepted',
            yookassa_id='pay-uuid-1',
        )
        self.team.tickets = 2
        self.team.save(update_fields=['tickets'])

        find_one_mock.return_value = self._fake_payment(self._payment_data(ticket.pk))

        response = self._post_webhook('payment.succeeded')
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 2)

    @patch('games.views.ticket.transaction.on_commit', side_effect=lambda fn: fn())
    @patch('games.telegram.notify.notify_payment_event')
    @patch('games.views.ticket.Payment.find_one')
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_canceled_rejects_pending(self, _cfg_mock, find_one_mock, notify_mock, _on_commit_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
            yookassa_id='pay-uuid-1',
        )
        find_one_mock.return_value = self._fake_payment(self._payment_data(ticket.pk))

        response = self._post_webhook('payment.canceled')

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Rejected')
        self.assertEqual(self.team.tickets, 0)
        notify_mock.assert_called_once()

    @patch('games.views.ticket.Payment.find_one')
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_without_metadata_logs_and_leaves_ticket_pending(self, _cfg_mock, find_one_mock):
        ticket = TicketRequest.objects.create(
            team=self.team,
            tickets=1,
            money=2000,
            status='Pending',
        )
        find_one_mock.return_value = self._fake_payment(
            {'id': 'pay-no-meta', 'description': 'legacy payment', 'metadata': {}},
        )

        with self.assertLogs('games.views.ticket', level='WARNING') as logs:
            response = self._post_webhook('payment.succeeded', payment_id='pay-no-meta')

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'Pending')
        self.assertTrue(any('missing metadata.ticket_request_id' in line for line in logs.output))

    @patch('games.views.ticket.Payment.find_one')
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_missing_ticket_request_logs_warning(self, _cfg_mock, find_one_mock):
        find_one_mock.return_value = self._fake_payment(self._payment_data(ticket_request_id=999999))

        with self.assertLogs('games.views.ticket', level='WARNING') as logs:
            response = self._post_webhook('payment.succeeded')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('ticket request not found' in line for line in logs.output))

    @patch('games.views.ticket.Payment.find_one', side_effect=RuntimeError('api down'))
    @patch('games.views.ticket.configure_yookassa_from_env')
    def test_webhook_payment_lookup_failure_logs_exception(self, _cfg_mock, _find_one_mock):
        with self.assertLogs('games.views.ticket', level='ERROR') as logs:
            response = self._post_webhook('payment.succeeded')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('Payment.find_one failed' in line for line in logs.output))
