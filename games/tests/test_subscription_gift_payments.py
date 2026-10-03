import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from allauth.socialaccount.models import SocialApp

from games.models import Profile, SubscriptionGift, SubscriptionGiftPayment, TributePurchase
from games.subscription_gifts import claim_gift, decrypt_gift_code
from games.subscription_gift_payments import (
    process_tribute_gift_purchase,
    process_tribute_gift_refund,
    process_yookassa_gift_event,
    process_yookassa_gift_refund,
    manually_resolve_subscription_gift_payment,
    reconcile_yookassa_gift_payments,
    start_yookassa_gift,
)
from games.tribute_util import compute_webhook_signature


GIFT_SETTINGS = {
    'TRIBUTE_ENABLED': True,
    'TRIBUTE_LEGAL_REVIEW_APPROVED': True,
    'TRIBUTE_MERCHANT': 'ru_self_employed',
    'TRIBUTE_API_KEY': 'gift-test-key',
    'TELEGRAM_BOT_TOKEN': 'test-token',
    'TELEGRAM_BOT_USERNAME': 'test_bot',
    'TRIBUTE_CLUB_GIFT_EUR_1_ID': '160748',
    'TRIBUTE_CLUB_GIFT_EUR_1_URL': 'https://web.tribute.tg/p/FOI',
    'TRIBUTE_CLUB_GIFT_EUR_1_AMOUNT': '555',
    'TRIBUTE_CLUB_GIFT_EUR_3_ID': '160751',
    'TRIBUTE_CLUB_GIFT_EUR_3_URL': 'https://web.tribute.tg/p/FOL',
    'TRIBUTE_CLUB_GIFT_EUR_3_AMOUNT': '1665',
    'CLUB_PAYMENTS_ENABLED': True,
    'CLUB_YOOKASSA_ENABLED': True,
    'CLUB_GIFT_YOOKASSA_1_AMOUNT_KOPECKS': 60000,
    'CLUB_GIFT_YOOKASSA_3_AMOUNT_KOPECKS': 180000,
}


@override_settings(**GIFT_SETTINGS)
class SubscriptionGiftPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site, _ = Site.objects.get_or_create(
            id=1, defaults={'domain': 'testserver', 'name': 'test'},
        )
        for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)

    def setUp(self):
        self.purchaser = User.objects.create_user('gift-buyer')
        Profile.objects.create(
            user=self.purchaser,
            first_name='Gift',
            last_name='Buyer',
            telegram_user_id=700001,
            telegram_username='giftbuyer',
            telegram_verified=True,
            telegram_linked_at=timezone.now(),
        )

    def _webhook(self, event='new_digital_product', **payload):
        body = json.dumps({'name': event, 'payload': payload}).encode()
        return self.client.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=compute_webhook_signature(body, 'gift-test-key'),
        )

    def _purchase_created_at(self):
        return timezone.now().isoformat()

    def test_gift_offers_are_visible_before_login(self):
        response = self.client.get(reverse('new_subscription'))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Международная карта или криптовалюта', body)
        self.assertIn('Российская карта', body)
        self.assertIn('€5.55', body)
        self.assertIn('600 ₽', body)
        self.assertIn('Войти, чтобы купить', body)

    def test_tribute_checkout_and_webhook_pays_gift(self):
        self.client.force_login(self.purchaser)
        response = self.client.post(
            reverse('new_subscription_gift_tribute_start'), {'months': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payment_url'], 'https://web.tribute.tg/p/FOI')
        payment = SubscriptionGiftPayment.objects.get()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_PENDING)

        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'telegram_username': 'giftbuyer',
            'purchase_id': 'gift-purchase-1',
            'transaction_id': 'gift-transaction-1',
            'purchase_created_at': self._purchase_created_at(),
        }
        webhook = self._webhook(**payload)
        self.assertEqual(webhook.status_code, 200)
        payment.refresh_from_db()
        gift = payment.gift
        gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_SUCCEEDED)
        self.assertEqual(gift.status, SubscriptionGift.STATUS_PAID)
        self.assertTrue(decrypt_gift_code(gift).startswith('IO-'))

        duplicate = self._webhook(**payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(SubscriptionGiftPayment.objects.count(), 1)

    def test_direct_tribute_purchase_creates_gift_when_pending_row_is_missing(self):
        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'telegram_username': 'giftbuyer',
            'purchase_id': 'gift-direct-purchase-1',
            'transaction_id': 'gift-direct-transaction-1',
            'purchase_created_at': self._purchase_created_at(),
        }
        self.assertTrue(process_tribute_gift_purchase(payload))
        payment = SubscriptionGiftPayment.objects.get(purchase_id=payload['purchase_id'])
        self.assertEqual(payment.purchaser, self.purchaser)
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_SUCCEEDED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_PAID)

    def test_paid_gift_can_be_claimed_by_recipient(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '3'})
        payment = SubscriptionGiftPayment.objects.get()
        self._webhook(
            product_id=160751,
            product_name='Подарочная подписка Inter Oves — 3 месяца',
            amount=1665,
            currency='eur',
            telegram_user_id=700001,
            telegram_username='giftbuyer',
            purchase_id='gift-purchase-3',
            transaction_id='gift-transaction-3',
            purchase_created_at=self._purchase_created_at(),
        )
        recipient = User.objects.create_user('gift-recipient')
        Profile.objects.create(user=recipient, first_name='Gift', last_name='Recipient')
        gift = payment.gift
        gift.refresh_from_db()
        claimed, entitlement = claim_gift(code=decrypt_gift_code(gift), user=recipient)
        self.assertEqual(claimed.status, SubscriptionGift.STATUS_CLAIMED)
        self.assertEqual(entitlement.ends_at.month, (entitlement.starts_at.month + 3 - 1) % 12 + 1)

    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_checkout_does_not_require_telegram_and_reuses_pending_payment(self, create):
        create.return_value = {
            'id': 'yk-gift-payment-1',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/1'},
        }
        user = User.objects.create_user('no-telegram-buyer')
        Profile.objects.create(user=user, first_name='No', last_name='Telegram')
        first = start_yookassa_gift(
            user=user, months=1, return_url='https://interoves.com/subscription/?payment=return',
        )
        second = start_yookassa_gift(
            user=user, months=1, return_url='https://interoves.com/subscription/?payment=return',
        )
        self.assertTrue(first.ok)
        self.assertEqual(first.payment_url, second.payment_url)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(SubscriptionGiftPayment.objects.count(), 1)

    def test_refund_revokes_access_after_gift_was_claimed(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'purchase_id': 'gift-purchase-refund',
            'transaction_id': 'gift-transaction-refund',
            'purchase_created_at': self._purchase_created_at(),
        }
        self._webhook(**payload)
        recipient = User.objects.create_user('refund-recipient')
        Profile.objects.create(user=recipient, first_name='Refund', last_name='Recipient')
        claim_gift(code=decrypt_gift_code(payment.gift), user=recipient)
        self.assertTrue(recipient.club_subscription.grants_access())

        self.assertTrue(process_tribute_gift_refund({
            'purchase_id': 'gift-purchase-refund',
            'refund_reason': 'test',
        }))
        payment.gift.refresh_from_db()
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_REVOKED)
        self.assertFalse(payment.gift.entitlements.filter(revoked_at__isnull=True).exists())
        recipient.club_subscription.refresh_from_db()
        self.assertFalse(recipient.club_subscription.grants_access())

    def test_tribute_success_cannot_resurrect_refunded_gift(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'purchase_id': 'gift-purchase-late-success',
            'transaction_id': 'gift-transaction-late-success',
            'purchase_created_at': self._purchase_created_at(),
        }
        self.assertTrue(process_tribute_gift_purchase(payload))
        self.assertTrue(process_tribute_gift_refund({'purchase_id': payload['purchase_id']}))
        self.assertTrue(process_tribute_gift_purchase(payload))
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_CANCELED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_REVOKED)

    def test_tribute_refund_received_before_purchase_cannot_pay_gift(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'telegram_username': 'giftbuyer',
            'purchase_id': 'gift-purchase-refund-first',
            'transaction_id': 'gift-transaction-refund-first',
            'refund_reason': 'test',
            'refunded_at': timezone.now().isoformat(),
        }
        refund_webhook = self._webhook(event='digital_product_refunded', **payload)
        self.assertEqual(refund_webhook.status_code, 200)
        self.assertTrue(process_tribute_gift_purchase(dict(
            payload, purchase_created_at=self._purchase_created_at(),
        )))
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_CANCELED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_REVOKED)
        self.assertEqual(
            TributePurchase.objects.get(purchase_id=payload['purchase_id']).status,
            TributePurchase.STATUS_REFUNDED,
        )

    def test_tribute_amount_mismatch_holds_matching_gift_for_review(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        payload = {
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 556,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'telegram_username': 'giftbuyer',
            'purchase_id': 'gift-purchase-mismatch',
            'transaction_id': 'gift-transaction-mismatch',
            'purchase_created_at': self._purchase_created_at(),
        }
        self.assertTrue(process_tribute_gift_purchase(payload))
        payment.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_MANUAL_REVIEW)
        self.assertTrue(manually_resolve_subscription_gift_payment(payment.pk, succeeded=True))
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_SUCCEEDED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_PAID)

    def test_tribute_purchase_for_expired_gift_goes_to_manual_review(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        payment.gift.status = SubscriptionGift.STATUS_EXPIRED
        payment.gift.save(update_fields=['status'])

        self.assertTrue(process_tribute_gift_purchase({
            'product_id': 160748,
            'product_name': 'Подарочная подписка Inter Oves — 1 месяц',
            'amount': 555,
            'currency': 'eur',
            'telegram_user_id': 700001,
            'purchase_id': 'gift-purchase-expired',
            'transaction_id': 'gift-transaction-expired',
            'purchase_created_at': self._purchase_created_at(),
        }))
        payment.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_MANUAL_REVIEW)
        payment.gift.refresh_from_db()
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_EXPIRED)

    def test_reveal_gift_code_requires_purchaser_and_does_not_embed_code(self):
        self.client.force_login(self.purchaser)
        self.client.post(reverse('new_subscription_gift_tribute_start'), {'months': '1'})
        payment = SubscriptionGiftPayment.objects.get()
        self._webhook(
            product_id=160748,
            product_name='Подарочная подписка Inter Oves — 1 месяц',
            amount=555,
            currency='eur',
            telegram_user_id=700001,
            telegram_username='giftbuyer',
            purchase_id='gift-purchase-reveal',
            transaction_id='gift-transaction-reveal',
            purchase_created_at=self._purchase_created_at(),
        )
        page = self.client.get(reverse('new_subscription'))
        self.assertNotContains(page, decrypt_gift_code(payment.gift))
        reveal = self.client.post(reverse(
            'new_subscription_gift_reveal', args=[payment.gift_id],
        ))
        self.assertEqual(reveal.status_code, 200)
        self.assertEqual(reveal.json()['code'], decrypt_gift_code(payment.gift))

    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_success_cannot_resurrect_canceled_gift(self, create):
        create.return_value = {
            'id': 'yk-gift-late-success',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/late'},
        }
        payment = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=return',
        ).payment
        canceled = {
            'id': payment.provider_payment_id,
            'metadata': {'purpose': 'club_gift', 'gift_payment_id': str(payment.pk)},
            'amount': {'value': '600.00', 'currency': 'RUB'},
        }
        self.assertTrue(process_yookassa_gift_event('payment.canceled', canceled))
        self.assertTrue(process_yookassa_gift_event('payment.succeeded', canceled))
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_CANCELED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_REVOKED)

    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_canceled_cannot_revoke_succeeded_gift(self, create):
        create.return_value = {
            'id': 'yk-gift-late-cancel',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/late-cancel'},
        }
        payment = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=gift-return',
        ).payment
        succeeded = {
            'id': payment.provider_payment_id,
            'metadata': {'purpose': 'club_gift', 'gift_payment_id': str(payment.pk)},
            'amount': {'value': '600.00', 'currency': 'RUB'},
        }
        self.assertTrue(process_yookassa_gift_event('payment.succeeded', succeeded))
        self.assertTrue(process_yookassa_gift_event('payment.canceled', succeeded))
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_SUCCEEDED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_PAID)

    @patch('games.subscription_gift_payments.Payment.find_one')
    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_reconciliation_confirms_manual_review_payment(self, create, find_one):
        create.return_value = {
            'id': 'yk-gift-reconcile',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/reconcile'},
        }
        payment = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=return',
        ).payment
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.save(update_fields=['status'])
        find_one.return_value = {
            'id': payment.provider_payment_id,
            'status': 'succeeded',
            'metadata': {'purpose': 'club_gift', 'gift_payment_id': str(payment.pk)},
            'amount': {'value': '600.00', 'currency': 'RUB'},
        }

        self.assertEqual(reconcile_yookassa_gift_payments(), 1)
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_SUCCEEDED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_PAID)

    @patch('games.subscription_gift_payments.Payment.find_one')
    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_reconciliation_cancels_manual_review_payment(self, create, find_one):
        create.return_value = {
            'id': 'yk-gift-reconcile-canceled',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/reconcile-canceled'},
        }
        payment = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=return',
        ).payment
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.save(update_fields=['status'])
        find_one.return_value = {
            'id': payment.provider_payment_id,
            'status': 'canceled',
            'metadata': {'purpose': 'club_gift', 'gift_payment_id': str(payment.pk)},
            'amount': {'value': '600.00', 'currency': 'RUB'},
        }

        self.assertEqual(reconcile_yookassa_gift_payments(), 1)
        payment.refresh_from_db()
        payment.gift.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_CANCELED)
        self.assertEqual(payment.gift.status, SubscriptionGift.STATUS_REVOKED)

    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_manual_review_prevents_duplicate_checkout(self, create):
        create.return_value = {
            'id': 'yk-gift-ambiguous',
            'confirmation': {},
        }
        first = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=gift-return',
        )
        second = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=gift-return',
        )
        self.assertFalse(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.http_status, 409)
        self.assertEqual(create.call_count, 1)

    @patch('games.subscription_gift_payments.Payment.create')
    def test_yookassa_partial_refund_goes_to_manual_review(self, create):
        create.return_value = {
            'id': 'yk-gift-refund',
            'confirmation': {'confirmation_url': 'https://yookassa.test/pay/refund'},
        }
        payment = start_yookassa_gift(
            user=self.purchaser, months=1,
            return_url='https://interoves.com/subscription/?payment=gift-return',
        ).payment
        self.assertFalse(process_yookassa_gift_refund({
            'payment_id': payment.provider_payment_id,
            'amount': {'value': '100.00', 'currency': 'RUB'},
        }))
        payment.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionGiftPayment.STATUS_MANUAL_REVIEW)
