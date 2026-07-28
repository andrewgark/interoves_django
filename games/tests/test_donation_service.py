"""Unit tests for donation_service helpers."""
from django.test import TestCase

from games.donation_service import (
    confirm_donation,
    extract_pay_fields,
    parse_donation_order_id,
    reject_donation,
)
from games.models import Donation


class DonationServiceTests(TestCase):
    def test_parse_donation_order_id(self):
        self.assertEqual(parse_donation_order_id('donation-12'), 12)
        self.assertIsNone(parse_donation_order_id('12'))
        self.assertIsNone(parse_donation_order_id('donation-x'))
        self.assertIsNone(parse_donation_order_id(None))

    def test_extract_pay_fields_prefers_pay_amount(self):
        amount, currency = extract_pay_fields({
            'pay_amount': '0.01',
            'pay_currency': 'BTC',
            'actually_paid': '9',
            'price_amount': 100,
            'price_currency': 'rub',
        })
        self.assertEqual(amount, '0.01')
        self.assertEqual(currency, 'btc')

    def test_confirm_and_reject_idempotent(self):
        donation = Donation.objects.create(amount_rub=100, status='Pending')
        first = confirm_donation(donation, pay_amount='1', pay_currency='usdt', source='test')
        self.assertTrue(first.changed)
        donation.refresh_from_db()
        self.assertEqual(donation.status, 'Confirmed')

        second = confirm_donation(donation, pay_amount='2', pay_currency='eth', source='test')
        self.assertFalse(second.changed)
        donation.refresh_from_db()
        self.assertEqual(donation.pay_amount, '1')
        self.assertEqual(donation.pay_currency, 'usdt')

        rejected = Donation.objects.create(amount_rub=50, status='Pending')
        self.assertTrue(reject_donation(rejected, source='test').changed)
        self.assertFalse(reject_donation(rejected, source='test').changed)

    def test_recent_donations_for_request_uses_session(self):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from games.donation_service import recent_donations_for_request, remember_donation_in_session

        d1 = Donation.objects.create(amount_rub=10, status='Pending')
        d2 = Donation.objects.create(amount_rub=20, status='Confirmed')
        factory = RequestFactory()
        request = factory.get('/donate/')
        request.user = AnonymousUser()
        request.session = SessionStore()
        remember_donation_in_session(request, d1.id)
        remember_donation_in_session(request, d2.id)
        recent = recent_donations_for_request(request)
        self.assertEqual([d.pk for d in recent], [d2.pk, d1.pk])

    def test_reject_stale_pending_donations(self):
        from datetime import timedelta

        from django.utils import timezone

        from games.donation_service import reject_stale_pending_donations

        fresh = Donation.objects.create(amount_rub=100, status='Pending')
        stale = Donation.objects.create(amount_rub=200, status='Pending')
        Donation.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(hours=9),
        )
        confirmed = Donation.objects.create(amount_rub=300, status='Confirmed')
        Donation.objects.filter(pk=confirmed.pk).update(
            created_at=timezone.now() - timedelta(hours=10),
        )

        rejected = reject_stale_pending_donations(hours=8)
        self.assertEqual(rejected, 1)
        fresh.refresh_from_db()
        stale.refresh_from_db()
        confirmed.refresh_from_db()
        self.assertEqual(fresh.status, 'Pending')
        self.assertEqual(stale.status, 'Rejected')
        self.assertEqual(confirmed.status, 'Confirmed')
