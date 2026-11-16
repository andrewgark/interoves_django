from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from games.club.billing.gifts.offers import claim_gift, create_gift, create_paid_gift
from games.club.billing.gifts.payments import (
    manually_resolve_subscription_gift_payment,
    process_yookassa_gift_event,
)
from games.models import (
    Profile,
    SubscriptionGift,
    SubscriptionGiftAdminAlertOutbox,
    SubscriptionGiftPayment,
)
from games.ops.workers.billing_outbox import dispatch_club_payment_admin_alert_outbox

PUBLISH = 'games.ops.telegram.bot.notify.publish_admin_alert'


class SubscriptionGiftAdminAlertTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', is_superuser=True)
        self.buyer = User.objects.create_user('gift-buyer')
        Profile.objects.create(
            user=self.buyer, first_name='Gift', last_name='Buyer',
            telegram_user_id=700001, telegram_username='giftbuyer', telegram_verified=True,
        )
        self.recipient = User.objects.create_user('gift-recipient')
        Profile.objects.create(
            user=self.recipient, first_name='Gift', last_name='Recipient',
            telegram_user_id=700002, telegram_username='giftrecipient', telegram_verified=True,
        )

    def _yookassa_gift(self):
        gift, code = create_paid_gift(
            purchaser=self.buyer, duration_months=1,
            provider=SubscriptionGift.PROVIDER_YOOKASSA, amount=60000, currency='RUB',
        )
        payment = SubscriptionGiftPayment.objects.create(
            gift=gift, purchaser=self.buyer, provider=SubscriptionGift.PROVIDER_YOOKASSA,
            idempotency_key='gift-alert-test', expected_amount=60000, currency='RUB',
            duration_months=1,
        )
        return gift, code, payment

    def _succeed(self, payment):
        process_yookassa_gift_event('payment.succeeded', {
            'id': 'yk-gift-1',
            'status': 'succeeded',
            'amount': {'value': '600.00', 'currency': 'RUB'},
            'metadata': {'purpose': 'club_gift', 'gift_payment_id': str(payment.pk)},
        })

    def _alerts(self):
        return list(SubscriptionGiftAdminAlertOutbox.objects.order_by('pk').values_list(
            'gift_id', 'alert_type', 'status',
        ))

    def test_paid_gift_records_one_alert_and_worker_sends_it(self):
        gift, _code, payment = self._yookassa_gift()
        self._succeed(payment)
        self._succeed(payment)  # duplicate webhook

        self.assertEqual(self._alerts(), [(gift.pk, 'paid', 'pending')])
        with patch(PUBLISH, return_value=True) as publish:
            result = dispatch_club_payment_admin_alert_outbox()

        self.assertEqual(result['sent'], 1)
        self.assertEqual(self._alerts(), [(gift.pk, 'paid', 'sent')])
        text = publish.call_args.kwargs['payload']['text']
        self.assertIn('Куплен подарок', text)
        self.assertIn('gift-buyer', text)
        self.assertIn('@giftbuyer', text)
        self.assertIn('600 RUB', text)
        self.assertIn('1 месяц', text)
        self.assertEqual(publish.call_args.kwargs['alert'], 'subscription_gift')

    def test_claimed_gift_records_alert_with_recipient_and_giver(self):
        gift, code, payment = self._yookassa_gift()
        self._succeed(payment)
        claim_gift(code=code, user=self.recipient)

        self.assertEqual(
            [row[:2] for row in self._alerts()], [(gift.pk, 'paid'), (gift.pk, 'claimed')],
        )
        with patch(PUBLISH, return_value=True) as publish:
            dispatch_club_payment_admin_alert_outbox()

        claimed_text = publish.call_args_list[-1].kwargs['payload']['text']
        self.assertIn('активирован', claimed_text)
        self.assertIn('Активировал: <b>gift-recipient</b>', claimed_text)
        self.assertIn('От: <b>gift-buyer</b>', claimed_text)
        self.assertIn('Доступ: до ', claimed_text)

    def test_admin_forever_gift_claim_is_reported_as_manual(self):
        created = create_gift(created_by=self.admin, is_forever=True)
        claim_gift(code=created.code, user=self.recipient)

        with patch(PUBLISH, return_value=True) as publish:
            dispatch_club_payment_admin_alert_outbox()

        text = publish.call_args.kwargs['payload']['text']
        self.assertIn('выдан вручную', text)
        self.assertIn('Доступ: навсегда', text)

    def test_failed_enqueue_is_retried_later(self):
        created = create_gift(created_by=self.admin, duration_months=1)
        claim_gift(code=created.code, user=self.recipient)

        with patch(PUBLISH, return_value=False):
            result = dispatch_club_payment_admin_alert_outbox()
        self.assertEqual(result['failed'], 1)
        row = SubscriptionGiftAdminAlertOutbox.objects.get()
        self.assertEqual(row.status, SubscriptionGiftAdminAlertOutbox.STATUS_PENDING)
        self.assertEqual(row.last_error, 'enqueue_returned_false')
        self.assertIsNotNone(row.next_attempt_at)

    def test_manual_resolution_records_paid_alert(self):
        gift, _code, payment = self._yookassa_gift()
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.save(update_fields=['status'])

        manually_resolve_subscription_gift_payment(payment.pk, succeeded=True)

        self.assertEqual(self._alerts(), [(gift.pk, 'paid', 'pending')])
