from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from games.club.metrics import club_period_stats, club_snapshot
from games.models import (
    ClubEntitlement,
    ClubSubscription,
    ClubSubscriptionEvent,
    ClubYooKassaPayment,
    SubscriptionGift,
)
from games.ops.telegram.bot.reports import build_admin_report

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 2, 0, 25, tzinfo=MOSCOW)


class ClubMetricsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('club-admin')
        self._seq = 0

    def _user(self):
        self._seq += 1
        return User.objects.create_user('club-user-{}'.format(self._seq))

    def _subscription(self, user, provider, *, auto_renew=True, paid_until=None, **extra):
        subscription = ClubSubscription.objects.create(
            user=user, provider=provider, status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=auto_renew, paid_until=paid_until or NOW + timedelta(days=20), **extra,
        )
        ClubSubscription.objects.filter(pk=subscription.pk).update(created_at=NOW - timedelta(days=60))
        return subscription

    def _entitlement(self, user, kind, *, created_at, ends_at=None, **links):
        row = ClubEntitlement.objects.create(
            user=user, kind=kind, starts_at=created_at,
            ends_at=ends_at if ends_at is not None else created_at + timedelta(days=30), **links,
        )
        ClubEntitlement.objects.filter(pk=row.pk).update(created_at=created_at)
        return row

    def _yookassa_paid(self, user, subscription, created_at, **kwargs):
        self._seq += 1
        payment = ClubYooKassaPayment.objects.create(
            user=user, club_subscription=subscription, kind='initial_monthly',
            period_key='p-{}'.format(self._seq), idempotency_key='k-{}'.format(self._seq), amount=42000,
        )
        return self._entitlement(
            user, ClubEntitlement.KIND_PAID, created_at=created_at,
            club_subscription=subscription, yookassa_payment=payment, **kwargs,
        )

    def _tribute_paid(self, user, subscription, created_at, **kwargs):
        self._seq += 1
        event = ClubSubscriptionEvent.objects.create(
            event_key='e-{}'.format(self._seq), event_name='new_subscription',
            club_subscription=subscription, result=ClubSubscriptionEvent.RESULT_APPLIED,
        )
        return self._entitlement(
            user, ClubEntitlement.KIND_PAID, created_at=created_at,
            club_subscription=subscription, tribute_event=event, **kwargs,
        )

    def _gift(self, user, *, purchased=False):
        self._seq += 1
        gift = SubscriptionGift.objects.create(
            code_hash='h-{}'.format(self._seq), duration_months=1, created_by=self.admin,
            purchaser=self.admin if purchased else None,
        )
        return self._entitlement(user, ClubEntitlement.KIND_GIFT, created_at=NOW - timedelta(days=3), gift=gift)

    def test_snapshot_splits_paid_by_payment_route_and_gifts_by_origin(self):
        yk = self._user()
        self._yookassa_paid(yk, self._subscription(yk, 'yookassa'), NOW - timedelta(days=5))
        # Provider field still says YooKassa but the paying entitlement is Tribute.
        switched = self._user()
        self._tribute_paid(switched, self._subscription(switched, 'yookassa'), NOW - timedelta(days=5))
        self._gift(self._user())
        self._gift(self._user(), purchased=True)
        manual = self._user()
        self._entitlement(manual, ClubEntitlement.KIND_MANUAL, created_at=NOW - timedelta(days=1), ends_at=None)
        ClubEntitlement.objects.filter(user=manual).update(ends_at=None)
        # A paid user who also holds a gift is counted once, as paid.
        both = self._user()
        self._yookassa_paid(both, self._subscription(both, 'yookassa'), NOW - timedelta(days=2))
        self._gift(both)
        # Expired and revoked access does not count.
        old = self._user()
        self._yookassa_paid(old, self._subscription(old, 'yookassa', paid_until=NOW - timedelta(days=1)),
                            NOW - timedelta(days=40))
        revoked = self._user()
        self._entitlement(revoked, ClubEntitlement.KIND_MANUAL, created_at=NOW - timedelta(days=1))
        ClubEntitlement.objects.filter(user=revoked).update(revoked_at=NOW - timedelta(hours=1))

        snapshot = club_snapshot(NOW)
        self.assertEqual((snapshot.paid_yookassa, snapshot.paid_tribute), (2, 1))
        self.assertEqual((snapshot.gift_admin, snapshot.gift_purchased, snapshot.manual), (1, 1, 1))
        self.assertEqual(snapshot.total, 6)

    def test_snapshot_counts_cancelled_auto_renew_and_payments_outside_the_ledger(self):
        leaving = self._user()
        sub = self._subscription(leaving, 'yookassa', auto_renew=False, paid_until=NOW + timedelta(days=3))
        self._yookassa_paid(leaving, sub, NOW - timedelta(days=27), ends_at=NOW + timedelta(days=3))
        legacy = self._user()
        self._subscription(legacy, 'tribute', last_payment_at=NOW - timedelta(days=10))
        snapshot = club_snapshot(NOW)
        self.assertEqual((snapshot.paid_yookassa, snapshot.paid_tribute), (1, 1))
        self.assertEqual((snapshot.paid_auto_renew_off, snapshot.paid_ending_soon), (1, 1))
        # A snapshot in the past must not count a subscription created later.
        ClubSubscription.objects.filter(user=legacy).update(created_at=NOW - timedelta(days=2))
        self.assertEqual(club_snapshot(NOW - timedelta(days=7)).paid_tribute, 0)

    def test_period_separates_first_payments_from_renewals(self):
        since, until = NOW - timedelta(days=1), NOW
        renewing = self._user()
        sub = self._subscription(renewing, 'tribute')
        self._tribute_paid(renewing, sub, NOW - timedelta(days=31))
        self._tribute_paid(renewing, sub, NOW - timedelta(hours=5))
        fresh = self._user()
        self._yookassa_paid(fresh, self._subscription(fresh, 'yookassa'), NOW - timedelta(hours=3))
        pre_ledger = self._user()
        self._yookassa_paid(
            pre_ledger,
            self._subscription(pre_ledger, 'yookassa', intro_offer_used_at=NOW - timedelta(days=33)),
            NOW - timedelta(hours=2),
        )
        cancelled = self._user()
        self._subscription(cancelled, 'yookassa', auto_renew=False, cancelled_at=NOW - timedelta(hours=1))

        stats = club_period_stats(since, until)
        self.assertEqual(stats.first_payments, {'yookassa': 1, 'tribute': 0})
        self.assertEqual(stats.renewals, {'yookassa': 1, 'tribute': 1})
        self.assertEqual(stats.auto_renew_cancellations, 1)

    @override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_ADMIN_CHAT_ID='12345')
    def test_admin_report_has_club_section(self):
        user = self._user()
        self._tribute_paid(user, self._subscription(user, 'tribute'), NOW - timedelta(hours=4))
        self._gift(self._user())
        text, _kind, _period = build_admin_report(now=NOW)
        self.assertIn('Клуб', text)
        self.assertIn('Активных подписок: 2', text)
        self.assertIn('Оплачено: 1 (ЮKassa 0 · Tribute 1)', text)
        self.assertIn('Подарки: 1 (выданы 1)', text)
        self.assertIn('Новые оплаты: 1 (ЮKassa 0 · Tribute 1)', text)
        self.assertLess(len(text), 4096)
