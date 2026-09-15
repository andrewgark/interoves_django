"""Real separate-connection races; run on MySQL/Postgres or file-backed SQLite.

SQLite shared-memory test databases fail immediately on concurrent table locks;
they cannot exercise waiting for a lock. Use a TEST.NAME file to run these there.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, current_thread
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from games import club_yookassa as billing
from games.models import ClubSubscription, ClubYooKassaPayment, SavedPaymentMethod


@override_settings(CLUB_YOOKASSA_ENABLED=True, YOOKASSA_RECURRING_ENABLED=True)
class ClubYooKassaRaceTests(TransactionTestCase):
    def setUp(self):
        name = str(connection.settings_dict['NAME'])
        if connection.vendor == 'sqlite' and ('memory' in name or name == ':memory:'):
            self.skipTest('Concurrent SQLite tests need a file-backed TEST.NAME (or MySQL/Postgres).')
        self.user = User.objects.create_user('race-owner')
        method = SavedPaymentMethod.objects.create(user=self.user, provider_payment_method_id='race-token')
        self.sub = ClubSubscription.objects.create(
            user=self.user, provider='yookassa', plan='monthly', status='active',
            auto_renew=True, saved_payment_method=method,
            paid_until=timezone.now() + timedelta(days=1),
            next_charge_at=timezone.now() - timedelta(seconds=1),
        )

    @staticmethod
    def _thread_call(function):
        connections.close_all()
        try:
            return function()
        finally:
            connections.close_all()

    def test_renewal_first_detach_waits_for_submission_then_blocks_all_new_charges(self):
        sending, release, detaching = Event(), Event(), Event()
        real_lock = billing._billing_lock

        def create(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            sending.set()
            if not release.wait(5):
                raise AssertionError('Test did not release provider response')
            return {'id': 'race-payment', 'status': 'pending'}

        @contextmanager
        def observed_lock(user_id):
            if current_thread().name.startswith('detach'):
                detaching.set()
            with real_lock(user_id):
                yield

        with patch.object(billing, '_billing_lock', observed_lock), \
                patch.object(billing, '_create_yookassa_payment', side_effect=create) as api, \
                ThreadPoolExecutor(1, thread_name_prefix='renew') as renew_pool, \
                ThreadPoolExecutor(1, thread_name_prefix='detach') as detach_pool:
            renewal = renew_pool.submit(self._thread_call, lambda: billing.renew_due_subscriptions())
            try:
                self.assertTrue(sending.wait(5))
                detach = detach_pool.submit(self._thread_call,
                                            lambda: billing.detach_yookassa_payment_method(self.user))
                self.assertTrue(detaching.wait(5))
                # Separate DB connection is at the lock while provider call is blocked.
                from concurrent.futures import TimeoutError
                with self.assertRaises(TimeoutError):
                    detach.result(timeout=0.2)
            finally:
                release.set()
            self.assertEqual(renewal.result(timeout=5)['created'], 1)
            self.assertIn('ещё может завершиться', detach.result(timeout=5).message)
            self.assertFalse(billing._renew_one(self.sub.pk, now=timezone.now()))
            self.assertEqual(billing.renew_due_subscriptions()['created'], 0)
            self.assertEqual(api.call_count, 1)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.grants_access())
        self.assertFalse(self.sub.auto_renew)
        self.assertFalse(SavedPaymentMethod.objects.exclude(provider_payment_method_id=None).exists())

    def test_detach_first_stale_scheduler_cannot_submit(self):
        waiting = Event()
        real_lock = billing._billing_lock

        @contextmanager
        def observed_lock(user_id):
            if current_thread().name.startswith('renew'):
                waiting.set()
            with real_lock(user_id):
                yield

        with patch.object(billing, '_billing_lock', observed_lock), \
                patch.object(billing, '_create_yookassa_payment') as api, \
                ThreadPoolExecutor(1, thread_name_prefix='renew') as pool:
            with real_lock(self.user.pk):
                # Scheduler already selected the subscription before the detach.
                future = pool.submit(self._thread_call,
                                     lambda: billing._renew_one(self.sub.pk, now=timezone.now()))
                self.assertTrue(waiting.wait(5))
                self.assertTrue(billing.detach_yookassa_payment_method(self.user).ok)
            self.assertFalse(future.result(timeout=5))
            api.assert_not_called()
        self.assertFalse(ClubYooKassaPayment.objects.exists())

    def test_detach_between_reservation_and_submission_blocks_dispatch(self):
        original_submit = billing._submit_renewal

        def detach_then_submit(payment_pk):
            self.assertTrue(billing.detach_yookassa_payment_method(self.user).ok)
            return original_submit(payment_pk)

        with patch.object(billing, '_submit_renewal', side_effect=detach_then_submit), \
                patch.object(billing, '_create_yookassa_payment') as api:
            self.assertEqual(billing.renew_due_subscriptions()['created'], 0)
            api.assert_not_called()
        self.assertEqual(ClubYooKassaPayment.objects.get().status, 'canceled')
