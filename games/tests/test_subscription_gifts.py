from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from games.club_access import has_club_access
from games.models import ClubEntitlement, Profile, SubscriptionGift
from games.subscription_gifts import claim_gift, create_gift
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

    def test_wrong_telegram_cannot_claim(self):
        other = User.objects.create_user('other')
        Profile.objects.create(user=other, first_name='Other', last_name='User', telegram_user_id=999)
        created = create_gift(
            created_by=self.admin,
            recipient_telegram_user_id=123456,
            duration_months=1,
        )

        with self.assertRaisesMessage(ValueError, 'привязанный к Telegram'):
            claim_gift(code=created.code, user=other)

    @patch('games.telegram.api.send_message', return_value=True)
    def test_admin_gift_command_creates_and_sends_gift(self, send_message):
        reply = handle_admin_command('/gift @gift_recipient 3')

        self.assertIn('на 3 месяцев', reply)
        gift = SubscriptionGift.objects.get()
        self.assertEqual(gift.duration_months, 3)
        self.assertIsNotNone(gift.sent_at)
        send_message.assert_called_once()
