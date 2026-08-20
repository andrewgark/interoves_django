import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import (
    HTMLPage,
    Profile,
    Project,
    Team,
    TelegramLinkToken,
    TicketRequest,
    TributePaymentIntent,
    TributePurchase,
)
from games.telegram_linking import TelegramLinkError, consume_link_token, create_link_token
from games.tribute_service import manually_issue_purchase
from games.tribute_util import compute_webhook_signature, verify_webhook_signature


TRIBUTE_SETTINGS = {
    'TRIBUTE_ENABLED': True,
    'TRIBUTE_LEGAL_REVIEW_APPROVED': True,
    'TRIBUTE_MERCHANT': 'ru_self_employed',
    'TRIBUTE_API_KEY': 'test-tribute-key',
    'TRIBUTE_REGULAR_PRODUCT_ID': '1001',
    'TRIBUTE_REGULAR_PRODUCT_WEB_URL': 'https://web.tribute.tg/p/regular-test',
    'TRIBUTE_REGULAR_PRODUCT_AMOUNT': '1500',
    'TRIBUTE_REGULAR_PRODUCT_CURRENCY': 'EUR',
    'TRIBUTE_DISCOUNT_PRODUCT_ID': '1002',
    'TRIBUTE_DISCOUNT_PRODUCT_WEB_URL': 'https://web.tribute.tg/p/discount-test',
    'TRIBUTE_DISCOUNT_PRODUCT_AMOUNT': '500',
    'TRIBUTE_DISCOUNT_PRODUCT_CURRENCY': 'EUR',
    'TELEGRAM_BOT_TOKEN': 'test-bot-token',
    'TELEGRAM_BOT_USERNAME': 'interoves_test_bot',
}


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
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
    def test_verify_valid_and_invalid_signatures(self):
        body = b'{"name":"new_digital_product","payload":{"purchase_id":1}}'
        signature = compute_webhook_signature(body, 'secret')
        self.assertTrue(verify_webhook_signature(body, signature, api_key='secret'))
        self.assertFalse(verify_webhook_signature(body, 'deadbeef', api_key='secret'))
        self.assertFalse(verify_webhook_signature(body, None, api_key='secret'))


@override_settings(**TRIBUTE_SETTINGS)
class TelegramLinkingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('link-user', password='secret')
        Profile.objects.create(user=cls.user, first_name='Link', last_name='User')

    def test_valid_token_links_numeric_identity(self):
        token, raw = create_link_token(self.user)
        result = consume_link_token(raw, telegram_user_id=123456789, telegram_username='new_name')
        self.user.profile.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(result.telegram_user_id, 123456789)
        self.assertTrue(self.user.profile.telegram_verified)
        self.assertEqual(self.user.profile.telegram_username, 'new_name')
        self.assertIsNotNone(token.used_at)

    @patch('games.telegram.webhook.send_message')
    def test_existing_bot_start_command_consumes_link(self, send_mock):
        from games.telegram.webhook import _dispatch_update

        _token, raw = create_link_token(self.user)
        _dispatch_update({
            'message': {
                'chat': {'id': 555001, 'type': 'private'},
                'from': {'id': 555001, 'username': 'linked_in_bot'},
                'text': '/start {}'.format(raw),
            },
        })
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.telegram_user_id, 555001)
        self.assertTrue(self.user.profile.telegram_verified)
        self.assertIn('успешно привязан', send_mock.call_args.args[1])

    def test_expired_token_is_rejected(self):
        token, raw = create_link_token(self.user)
        TelegramLinkToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(TelegramLinkError) as raised:
            consume_link_token(raw, telegram_user_id=1)
        self.assertEqual(raised.exception.reason, 'expired_token')

    def test_reused_token_is_rejected(self):
        _token, raw = create_link_token(self.user)
        consume_link_token(raw, telegram_user_id=2)
        with self.assertRaises(TelegramLinkError) as raised:
            consume_link_token(raw, telegram_user_id=2)
        self.assertEqual(raised.exception.reason, 'used_token')

    def test_identity_already_owned_by_another_account(self):
        other = User.objects.create_user('link-other')
        Profile.objects.create(
            user=other,
            first_name='Other',
            last_name='User',
            telegram_user_id=99,
            telegram_verified=True,
            telegram_linked_at=timezone.now(),
        )
        _token, raw = create_link_token(self.user)
        with self.assertRaises(TelegramLinkError) as raised:
            consume_link_token(raw, telegram_user_id=99)
        self.assertEqual(raised.exception.reason, 'identity_in_use')

    def test_link_start_requires_login_and_redirects_to_bot(self):
        response = self.client.post(reverse('new_telegram_link_start'))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        response = self.client.post(reverse('new_telegram_link_start'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://t.me/interoves_test_bot?start='))


@override_settings(**TRIBUTE_SETTINGS)
class TributeDigitalProductTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('tribute-user', 'tribute@example.com', 'secret')
        Profile.objects.create(
            user=cls.user,
            first_name='Tribute',
            last_name='User',
            telegram_user_id=777001,
            telegram_username='old_username',
            telegram_verified=True,
            telegram_linked_at=timezone.now(),
        )
        cls.team = Team.objects.create(
            name='tribute_team',
            visible_name='Tribute Team',
            project_id='main',
            tickets=0,
            ticket_price=2000,
        )
        cls.user.profile.add_team_membership(cls.team, make_primary=True)
        cls.unlinked = User.objects.create_user('unlinked-user', password='secret')
        Profile.objects.create(user=cls.unlinked, first_name='No', last_name='Telegram')

    def setUp(self):
        self.http = Client()
        self.http.force_login(self.user)

    def _purchase_payload(self, **overrides):
        payload = {
            'product_id': 1001,
            'product_name': 'Inter Oves — 1 team ticket',
            'amount': 1500,
            'currency': 'eur',
            'trb_user_id': 'T-31326',
            'telegram_user_id': 777001,
            'telegram_username': 'old_username',
            'purchase_id': 78901,
            'transaction_id': 234567,
            'purchase_created_at': '2026-08-21T12:00:00Z',
        }
        payload.update(overrides)
        return payload

    def _refund_payload(self, **overrides):
        payload = self._purchase_payload()
        payload.pop('purchase_created_at')
        payload.update({
            'refund_reason': 'card_refund',
            'refunded_at': '2026-08-21T13:00:00Z',
        })
        payload.update(overrides)
        return payload

    def _post(self, payload, *, event='new_digital_product', signature=True, raw=None):
        body = raw if raw is not None else json.dumps({
            'name': event,
            'created_at': '2026-08-21T12:00:01Z',
            'sent_at': '2026-08-21T12:00:02Z',
            'payload': payload,
        }).encode()
        sig = compute_webhook_signature(body, TRIBUTE_SETTINGS['TRIBUTE_API_KEY']) if signature else 'invalid'
        return self.http.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=sig,
        )

    def _create_intent(self, *, team=None):
        chosen = team or self.team
        response = self.http.post(reverse('new_create_tribute_ticket_payment'), {
            'tickets': '1',
            'team_id': chosen.pk,
        })
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), TributePaymentIntent.objects.get(pk=response.json()['intent_id'])

    def test_checkout_visible_and_unlinked_user_gets_link_cta(self):
        response = self.http.get(reverse('new_pay'))
        body = response.content.decode()
        self.assertIn('Международная карта через Tribute', body)
        self.assertIn('15 EUR', body)
        self.assertIn('Telegram подтвержден', body)

        self.http.force_login(self.unlinked)
        response = self.http.get(reverse('new_pay'))
        self.assertIn('Сначала привяжите Telegram', response.content.decode())

    def test_linked_user_creates_and_reuses_single_intent(self):
        first, intent = self._create_intent()
        second, repeated = self._create_intent()
        self.assertEqual(first['payment_url'], 'https://web.tribute.tg/p/regular-test')
        self.assertEqual(intent.pk, repeated.pk)
        self.assertTrue(second['reused'])
        self.assertEqual(TributePaymentIntent.objects.filter(status='awaiting_payment').count(), 1)

    def test_selected_team_must_belong_to_user(self):
        foreign = Team.objects.create(name='foreign_team', project_id='main')
        response = self.http.post(reverse('new_create_tribute_ticket_payment'), {
            'tickets': '1',
            'team_id': foreign.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['reason'], 'team_forbidden')

    def test_quantity_greater_than_one_is_rejected(self):
        response = self.http.post(reverse('new_create_tribute_ticket_payment'), {
            'tickets': '2', 'team_id': self.team.pk,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reason'], 'quantity')

    def test_discount_product_requires_current_team_discount(self):
        self.team.ticket_price = 500
        self.team.save(update_fields=['ticket_price'])
        data, intent = self._create_intent()
        self.assertEqual(intent.ticket_type, 'discount')
        self.assertEqual(intent.expected_product_id, 1002)
        self.assertEqual(data['payment_url'], 'https://web.tribute.tg/p/discount-test')

    def test_valid_numeric_identity_purchase_issues_exactly_one_ticket(self):
        _data, intent = self._create_intent()
        response = self._post(self._purchase_payload())
        self.assertEqual(response.status_code, 200)
        purchase = TributePurchase.objects.get(purchase_id='78901')
        intent.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(purchase.status, 'issued')
        self.assertEqual(purchase.matched_user, self.user)
        self.assertEqual(purchase.matched_team, self.team)
        self.assertEqual(intent.status, 'completed')
        self.assertEqual(self.team.tickets, 1)
        self.assertEqual(purchase.ticket_request.payment_provider, 'tribute_digital')

    def test_changed_username_same_numeric_id_still_matches(self):
        self._create_intent()
        response = self._post(self._purchase_payload(telegram_username='renamed'))
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.telegram_username, 'renamed')
        self.assertEqual(TributePurchase.objects.get().status, 'issued')

    def test_username_only_event_never_auto_matches(self):
        self._create_intent()
        response = self._post(self._purchase_payload(telegram_user_id=None))
        self.assertEqual(response.status_code, 200)
        purchase = TributePurchase.objects.get()
        self.assertEqual(purchase.status, 'manual_review')
        self.assertEqual(purchase.review_reason, 'missing_telegram_identity')
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)

    def test_unknown_identity_and_no_intent_are_reviewed(self):
        response = self._post(self._purchase_payload(telegram_user_id=999999))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TributePurchase.objects.get().review_reason, 'unknown_telegram_identity')

        TributePurchase.objects.all().delete()
        response = self._post(self._purchase_payload(purchase_id=78902, transaction_id=234568))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TributePurchase.objects.get().review_reason, 'no_active_intent')

    def test_unknown_product_amount_and_currency_mismatches_are_reviewed(self):
        cases = (
            ({'product_id': 9999}, 'unknown_product'),
            ({'amount': 1501}, 'invalid_amount'),
            ({'currency': 'RUB'}, 'invalid_currency'),
        )
        for index, (changes, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                payload = self._purchase_payload(
                    purchase_id=79000 + index,
                    transaction_id=24000 + index,
                    **changes,
                )
                response = self._post(payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(TributePurchase.objects.get(purchase_id=str(79000 + index)).review_reason, reason)

    def test_duplicate_delivery_does_not_issue_second_ticket(self):
        self._create_intent()
        self.assertEqual(self._post(self._purchase_payload()).status_code, 200)
        self.assertEqual(self._post(self._purchase_payload()).status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 1)
        self.assertEqual(TributePurchase.objects.count(), 1)

    def test_active_intent_database_constraint(self):
        _data, intent = self._create_intent()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TributePaymentIntent.objects.create(
                user=intent.user,
                team=intent.team,
                ticket_request=TicketRequest.objects.create(
                    team=intent.team,
                    created_by=intent.user,
                    money=15,
                    tickets=1,
                    payment_provider='tribute_digital',
                    currency='EUR',
                ),
                telegram_user_id=intent.telegram_user_id,
                expected_product_id=intent.expected_product_id,
                expected_amount=intent.expected_amount,
                expected_currency='EUR',
                ticket_type='regular',
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def test_discount_paid_after_eligibility_removed_goes_to_review(self):
        self.team.ticket_price = 500
        self.team.save(update_fields=['ticket_price'])
        self._create_intent()
        self.team.ticket_price = 2000
        self.team.save(update_fields=['ticket_price'])
        response = self._post(self._purchase_payload(
            product_id=1002,
            amount=500,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TributePurchase.objects.get().review_reason, 'discount_ineligible')

    def test_direct_purchase_can_be_manually_attached_and_issued(self):
        response = self._post(self._purchase_payload())
        self.assertEqual(response.status_code, 200)
        purchase = TributePurchase.objects.get()
        purchase.matched_user = self.user
        purchase.matched_team = self.team
        purchase.save(update_fields=['matched_user', 'matched_team'])
        result = manually_issue_purchase(purchase.pk)
        self.assertTrue(result.ticket_issued)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 1)

    def test_valid_invalid_and_malformed_security_responses(self):
        response = self._post(self._purchase_payload(), signature=False)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(TributePurchase.objects.exists())

        bad_body = b'{not json'
        response = self._post({}, raw=bad_body)
        self.assertEqual(response.status_code, 400)

        response = self._post({'product_id': 1001})
        self.assertEqual(response.status_code, 400)

    def test_historical_shop_order_webhook_remains_compatible(self):
        ticket = TicketRequest.objects.create(
            team=self.team,
            created_by=self.user,
            money=20,
            tickets=1,
            status='Pending',
            tribute_id='legacy-order-uuid',
            payment_provider='tribute',
            merchant='legacy_unspecified',
        )
        response = self._post({
            'uuid': 'legacy-order-uuid',
            'status': 'paid',
        }, event='shop_order')
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.team.refresh_from_db()
        self.assertEqual(ticket.status, 'Accepted')
        self.assertEqual(self.team.tickets, 1)

    def test_refund_revokes_available_ticket_and_is_idempotent(self):
        self._create_intent()
        self._post(self._purchase_payload())
        response = self._post(self._refund_payload(), event='digital_product_refunded')
        self.assertEqual(response.status_code, 200)
        purchase = TributePurchase.objects.get()
        self.team.refresh_from_db()
        self.assertEqual(purchase.status, 'refunded')
        self.assertIsNotNone(purchase.ticket_revoked_at)
        self.assertEqual(self.team.tickets, 0)
        self.assertEqual(purchase.ticket_request.status, 'Accepted')

        response = self._post(self._refund_payload(), event='digital_product_refunded')
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.tickets, 0)

    def test_refund_after_ticket_used_keeps_history_and_flags_review(self):
        self._create_intent()
        self._post(self._purchase_payload())
        Team.objects.filter(pk=self.team.pk).update(tickets=0)
        response = self._post(self._refund_payload(), event='digital_product_refunded')
        self.assertEqual(response.status_code, 200)
        purchase = TributePurchase.objects.get()
        self.assertTrue(purchase.accounting_review_required)
        self.assertEqual(purchase.review_reason, 'ticket_already_used')
        self.assertEqual(purchase.ticket_request.status, 'Accepted')


class TributeDisabledByDefaultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()

    def test_route_card_is_present_but_not_enabled_without_real_configuration(self):
        response = self.client.get(reverse('new_pay'))
        body = response.content.decode()
        self.assertIn('Международная карта через Tribute', body)
        self.assertIn('Ожидает настройки товара', body)
        self.assertIn('value="tribute_card"', body)
        self.assertIn('value="tribute_card"', body)
        self.assertIn('new-pay-method--unavailable', body)
