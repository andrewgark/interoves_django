from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from games.club_access import has_club_access
from games.models import ClubEntitlement, Profile, SubscriptionGift
from games.subscription_gift_payments import create_paid_gift
from games.subscription_gifts import claim_gift, create_gift, gift_duration_label
from games.telegram.admin_commands import handle_admin_command


class SubscriptionGiftTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', is_superuser=True)
        self.user = User.objects.create_user('gift-recipient')
        Profile.objects.create(
            user=self.user,
            first_name='Gift',
            last_name='Recipient',
            telegram_user_id=123456,
            telegram_verified=True,
            telegram_username='gift_recipient',
        )

    def test_month_gift_is_claimed_once_and_grants_access(self):
        created = create_gift(
            created_by=self.admin,
            recipient_telegram_user_id=123456,
            recipient_telegram_username='gift_recipient',
            duration_months=1,
        )

        gift, entitlement = claim_gift(code=created.code, user=self.user)

        self.assertEqual(gift.status, SubscriptionGift.STATUS_CLAIMED)
        self.assertEqual(entitlement.kind, ClubEntitlement.KIND_GIFT)
        self.assertTrue(entitlement.ends_at > entitlement.starts_at)
        self.assertTrue(has_club_access(self.user))
        with self.assertRaisesMessage(ValueError, 'уже использован'):
            claim_gift(code=created.code, user=self.user)

    def test_forever_gift_has_no_end(self):
        created = create_gift(
            created_by=self.admin,
            recipient_telegram_user_id=123456,
            is_forever=True,
        )

        _, entitlement = claim_gift(code=created.code, user=self.user)

        self.assertIsNone(entitlement.ends_at)
        self.assertTrue(has_club_access(self.user))

    def test_unpaid_paid_provider_gift_cannot_be_claimed(self):
        gift, code = create_paid_gift(
            purchaser=self.admin,
            duration_months=1,
            provider=SubscriptionGift.PROVIDER_TRIBUTE,
            amount=555,
            currency='EUR',
        )
        with self.assertRaisesMessage(ValueError, 'ещё не оплачен'):
            claim_gift(code=code, user=self.user)
        gift.refresh_from_db()
        self.assertEqual(gift.status, SubscriptionGift.STATUS_CREATED)

    def test_gift_starts_after_existing_entitlement(self):
        now = timezone.now()
        ClubEntitlement.objects.create(
            user=self.user,
            kind=ClubEntitlement.KIND_PAID,
            starts_at=now,
            ends_at=now.replace(year=now.year + 1),
        )
        created = create_gift(
            created_by=self.admin,
            recipient_telegram_user_id=123456,
            duration_months=1,
        )

        _, entitlement = claim_gift(code=created.code, user=self.user)

        self.assertGreaterEqual(entitlement.starts_at, now.replace(year=now.year + 1))

    def test_addressed_gift_cannot_be_claimed_by_wrong_telegram(self):
        other = User.objects.create_user('other')
        Profile.objects.create(user=other, first_name='Other', last_name='User', telegram_user_id=999)
        created = create_gift(
            created_by=self.admin,
            recipient_telegram_user_id=123456,
            duration_months=1,
        )

        with self.assertRaisesMessage(ValueError, 'предназначен другому Telegram'):
            claim_gift(code=created.code, user=other)

    def test_unlinked_gift_can_be_claimed_once_by_an_inter_oves_account(self):
        unlinked = User.objects.create_user('unlinked-recipient')
        Profile.objects.create(user=unlinked, first_name='Unlinked', last_name='Recipient')
        created = create_gift(created_by=self.admin, duration_months=1)

        claim_gift(code=created.code, user=unlinked)
        with self.assertRaisesMessage(ValueError, 'уже использован'):
            claim_gift(code=created.code, user=self.user)

    @patch('games.telegram.api.send_message', return_value=True)
    def test_admin_gift_command_creates_and_sends_gift(self, send_message):
        reply = handle_admin_command('/gift @gift_recipient 3')

        self.assertIn('на 3 месяца', reply)
        gift = SubscriptionGift.objects.get()
        self.assertEqual(gift.duration_months, 3)
        self.assertIsNotNone(gift.sent_at)
        send_message.assert_called_once()

    def test_gift_duration_label_uses_russian_month_forms(self):
        expected = {
            1: '1 месяц',
            2: '2 месяца',
            4: '4 месяца',
            5: '5 месяцев',
            11: '11 месяцев',
            21: '21 месяц',
            24: '24 месяца',
        }
        for months, label in expected.items():
            gift = SubscriptionGift(duration_months=months, is_forever=False)
            self.assertEqual(gift_duration_label(gift), label)

    def test_admin_gift_command_can_create_manual_code(self):
        reply = handle_admin_command('/gift 3')

        self.assertIn('без привязки Telegram', reply)
        self.assertIn('Код:', reply)
        self.assertIn('Сообщение для получателя:', reply)
        self.assertIn('Перешлите этот текст получателю вручную.', reply)
        self.assertIsNone(SubscriptionGift.objects.get().recipient_telegram_user_id)

    def test_admin_gift_command_rejects_unknown_recipient(self):
        reply = handle_admin_command('/gift @missing 3')

        self.assertIn('Получатель не найден', reply)
        self.assertFalse(SubscriptionGift.objects.exists())
