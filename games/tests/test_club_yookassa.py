import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.club_access import has_club_access
from games.club_yookassa import (
    AMOUNT_INTRO_KOPECKS,
    AMOUNT_MONTHLY_KOPECKS,
    _submit_renewal,
    add_calendar_months,
    cancel_yookassa_subscription,
    detach_yookassa_payment_method,
    initial_monthly_amount_kopecks,
    process_yookassa_club_payment_event,
    renew_due_subscriptions,
    resume_yookassa_subscription,
    start_monthly_subscription,
)
from games.models import ClubSubscription, ClubYooKassaPayment, HTMLPage, Profile, Project, SavedPaymentMethod


YK_SETTINGS = {
    'CLUB_PAYMENTS_ENABLED': True,
    'CLUB_ARCHIVE_GATING_ENABLED': True,
    'CLUB_YOOKASSA_ENABLED': True,
    'YOOKASSA_RECURRING_ENABLED': True,
    'TRIBUTE_API_KEY': 'test-tribute-key',
    'TELEGRAM_BOT_TOKEN': 'test-bot-token',
    'TELEGRAM_BOT_USERNAME': 'interoves_test_bot',
}


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
    for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
        app, created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        if created:
            app.sites.add(site)


def _payment_payload(local: ClubYooKassaPayment, *, status='succeeded', saved=True, amount=None):
    amount = amount if amount is not None else local.amount
    return {
        'id': local.yookassa_payment_id or 'yk-pay-1',
        'status': status,
        'amount': {'value': '{:.2f}'.format(Decimal(amount) / 100), 'currency': 'RUB'},
        'metadata': {
            'purpose': 'club_subscription',
            'subscription_id': str(local.club_subscription_id),
            'subscription_payment_id': str(local.pk),
            'user_id': str(local.user_id),
            'kind': local.kind,
        },
        'payment_method': {
            'id': 'pm-saved-1',
            'saved': saved,
            'type': 'bank_card',
        },
        'cancellation_details': {'party': 'yoo_money', 'reason': 'insufficient_funds'},
    }


@override_settings(**YK_SETTINGS)
class ClubYooKassaPricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('yk-user', password='secret')
        Profile.objects.create(user=cls.user, first_name='Y', last_name='K')

    def test_new_user_gets_intro_price(self):
        self.assertEqual(initial_monthly_amount_kopecks(None), AMOUNT_INTRO_KOPECKS)

    def test_failed_intro_keeps_offer(self):
        sub = ClubSubscription.objects.create(
            user=self.user,
            provider=ClubSubscription.PROVIDER_YOOKASSA,
            status=ClubSubscription.STATUS_EXPIRED,
        )
        self.assertEqual(initial_monthly_amount_kopecks(sub), AMOUNT_INTRO_KOPECKS)

    def test_successful_intro_blocks_repeat(self):
        sub = ClubSubscription.objects.create(
            user=self.user,
            provider=ClubSubscription.PROVIDER_YOOKASSA,
            intro_offer_used_at=timezone.now(),
            status=ClubSubscription.STATUS_EXPIRED,
        )
        self.assertEqual(initial_monthly_amount_kopecks(sub), AMOUNT_MONTHLY_KOPECKS)


@override_settings(**YK_SETTINGS)
class ClubYooKassaFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('yk-flow', password='secret')
        Profile.objects.create(user=cls.user, first_name='Y', last_name='F')

    def _mock_create(self, *, payment_id='yk-1', url='https://yookassa.test/confirm'):
        payment = MagicMock()
        payment.__iter__ = lambda self: iter({
            'id': payment_id,
            'status': 'pending',
            'confirmation': {'confirmation_url': url},
            'payment_method': {'id': 'pm-1', 'saved': False},
        }.items())
        # dict(payment) uses keys from Mapping - configure as dict-like
        data = {
            'id': payment_id,
            'status': 'pending',
            'confirmation': {'confirmation_url': url},
            'payment_method': {'id': 'pm-1', 'saved': False},
        }

        class P(dict):
            pass

        return P(data)

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_initial_monthly_succeeds_and_saves_method(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        result = start_monthly_subscription(
            self.user, return_url='https://interoves.com/subscription/?payment=return',
        )
        self.assertTrue(result.ok)
        local = ClubYooKassaPayment.objects.get()
        self.assertEqual(local.amount, AMOUNT_INTRO_KOPECKS)
        self.assertEqual(local.kind, ClubYooKassaPayment.KIND_INITIAL_MONTHLY)
        payload = create_mock.call_args[0][0]
        self.assertTrue(payload['save_payment_method'])
        self.assertEqual(payload['amount']['value'], '420.00')

        process_yookassa_club_payment_event(
            'payment.succeeded',
            _payment_payload(local, saved=True),
        )
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertTrue(has_club_access(self.user))
        self.assertEqual(sub.status, ClubSubscription.STATUS_ACTIVE)
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.saved_payment_method.provider_payment_method_id, 'pm-saved-1')
        self.assertIsNotNone(sub.intro_offer_used_at)
        self.assertEqual(sub.amount, AMOUNT_INTRO_KOPECKS)
        # calendar month
        self.assertGreater(sub.paid_until, timezone.now() + timedelta(days=27))

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_initial_canceled_does_not_activate_or_consume_intro(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        start_monthly_subscription(self.user, return_url='https://x/return')
        local = ClubYooKassaPayment.objects.get()
        process_yookassa_club_payment_event(
            'payment.canceled',
            _payment_payload(local, status='canceled'),
        )
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertFalse(has_club_access(self.user))
        self.assertIsNone(sub.intro_offer_used_at)
        self.assertEqual(initial_monthly_amount_kopecks(sub), AMOUNT_INTRO_KOPECKS)

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_resubscribe_after_intro_is_900(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create(payment_id='yk-a')
        start_monthly_subscription(self.user, return_url='https://x/return')
        local = ClubYooKassaPayment.objects.get()
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        sub = ClubSubscription.objects.get(user=self.user)
        sub.paid_until = timezone.now() - timedelta(days=1)
        sub.auto_renew = False
        sub.status = ClubSubscription.STATUS_EXPIRED
        sub.save()
        create_mock.return_value = self._mock_create(payment_id='yk-b', url='https://yookassa.test/b')
        result = start_monthly_subscription(self.user, return_url='https://x/return')
        self.assertTrue(result.ok)
        second = ClubYooKassaPayment.objects.exclude(pk=local.pk).get()
        self.assertEqual(second.amount, AMOUNT_MONTHLY_KOPECKS)
        payload = create_mock.call_args[0][0]
        self.assertEqual(payload['amount']['value'], '600.00')

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_cancel_keeps_access_until_paid_through(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        start_monthly_subscription(self.user, return_url='https://x/return')
        local = ClubYooKassaPayment.objects.get()
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        cancel_yookassa_subscription(self.user)
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertFalse(sub.auto_renew)
        self.assertTrue(has_club_access(self.user))
        self.assertEqual(sub.effective_status(), ClubSubscription.STATUS_CANCELLED)
        resume_yookassa_subscription(self.user)
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.effective_status(), ClubSubscription.STATUS_ACTIVE)

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_duplicate_webhook_is_idempotent(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        start_monthly_subscription(self.user, return_url='https://x/return')
        local = ClubYooKassaPayment.objects.get()
        payload = _payment_payload(local)
        process_yookassa_club_payment_event('payment.succeeded', payload)
        paid_until = ClubSubscription.objects.get(user=self.user).paid_until
        process_yookassa_club_payment_event('payment.succeeded', payload)
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.paid_until, paid_until)
        self.assertEqual(ClubYooKassaPayment.objects.filter(status='succeeded').count(), 1)

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_renewal_extends_calendar_month(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        start_monthly_subscription(self.user, return_url='https://x/return')
        local = ClubYooKassaPayment.objects.get()
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        sub = ClubSubscription.objects.get(user=self.user)
        first_end = sub.paid_until
        sub.next_charge_at = timezone.now() - timedelta(minutes=1)
        sub.save(update_fields=['next_charge_at'])

        renew_payment = {
            'id': 'yk-renew-1',
            'status': 'succeeded',
            'amount': {'value': '600.00', 'currency': 'RUB'},
            'metadata': {},
            'payment_method': {'id': 'pm-saved-1', 'saved': True},
        }

        def create_side_effect(payload, key):
            local_pending = ClubYooKassaPayment.objects.filter(
                kind=ClubYooKassaPayment.KIND_RECURRING_MONTHLY,
            ).latest('pk')
            renew_payment['metadata'] = {
                'purpose': 'club_subscription',
                'subscription_payment_id': str(local_pending.pk),
                'subscription_id': str(sub.pk),
                'user_id': str(self.user.pk),
                'kind': ClubYooKassaPayment.KIND_RECURRING_MONTHLY,
            }
            return renew_payment

        create_mock.side_effect = create_side_effect
        stats = renew_due_subscriptions(limit=10)
        self.assertEqual(stats['created'], 1)
        sub.refresh_from_db()
        self.assertEqual(sub.paid_until, add_calendar_months(first_end, 1))
        # second renew same period should skip
        stats2 = renew_due_subscriptions(limit=10)
        self.assertEqual(stats2['created'], 0)

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_double_click_reuses_pending_payment(self, create_mock, _cfg):
        create_mock.return_value = self._mock_create()
        first = start_monthly_subscription(self.user, return_url='https://x/return')
        second = start_monthly_subscription(self.user, return_url='https://x/return')
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(first.confirmation_url, second.confirmation_url)
        self.assertEqual(ClubYooKassaPayment.objects.count(), 1)
        self.assertEqual(create_mock.call_count, 1)

    def test_page_shows_yookassa_pricing(self):
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('420 ₽', body)
        self.assertIn('600 ₽', body)
        self.assertNotIn('6 000 ₽', body)
        self.assertIn('последние 7 заданий', body)

    def test_endpoints_require_auth(self):
        for name in (
            'new_subscription_yookassa_monthly',
            'new_subscription_yookassa_cancel',
            'new_subscription_yookassa_resume',
        ):
            response = self.client.post(reverse(name))
            self.assertEqual(response.status_code, 401)

    @override_settings(YOOKASSA_RECURRING_ENABLED=False)
    @patch('games.club_yookassa.Payment.create')
    def test_recurring_flag_blocks_charges(self, create_mock):
        ClubSubscription.objects.create(
            user=self.user,
            provider=ClubSubscription.PROVIDER_YOOKASSA,
            plan=ClubSubscription.PLAN_MONTHLY,
            auto_renew=True,
            saved_payment_method=SavedPaymentMethod.objects.create(
                user=self.user, provider_payment_method_id='pm-1',
            ),
            next_charge_at=timezone.now() - timedelta(minutes=1),
            paid_until=timezone.now() + timedelta(days=1),
            status=ClubSubscription.STATUS_ACTIVE,
        )
        stats = renew_due_subscriptions()
        self.assertEqual(stats['created'], 0)
        create_mock.assert_not_called()


@override_settings(**YK_SETTINGS)
class ClubYooKassaDetachTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('detach-owner')
        Profile.objects.create(user=cls.user)
        cls.other = User.objects.create_user('detach-other')

    def setUp(self):
        self.method = SavedPaymentMethod.objects.create(
            user=self.user, provider_payment_method_id='secret-old-token', card_last4='1234',
        )
        self.sub = ClubSubscription.objects.create(
            user=self.user, provider='yookassa', plan='monthly', status='active',
            saved_payment_method=self.method, auto_renew=True,
            paid_until=timezone.now() + timedelta(days=10), next_charge_at=timezone.now(),
            intro_offer_used_at=timezone.now() - timedelta(days=20),
        )
        self.url = reverse('new_subscription_payment_method_detach')

    def test_detach_erases_all_tokens_atomically_keeps_access_and_audits(self):
        stale = SavedPaymentMethod.objects.create(
            user=self.user, provider_payment_method_id='stale-token', is_active=False,
        )
        with self.assertLogs('games.club_yookassa') as logs, self.captureOnCommitCallbacks(execute=True):
            result = detach_yookassa_payment_method(self.user)
        self.assertTrue(result.ok)
        self.method.refresh_from_db()
        stale.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertIsNone(self.method.provider_payment_method_id)
        self.assertIsNone(stale.provider_payment_method_id)
        self.assertFalse(self.method.is_active)
        self.assertIsNotNone(self.method.detached_at)
        self.assertEqual(self.method.card_last4, '')
        self.assertIsNone(self.sub.saved_payment_method_id)
        self.assertFalse(self.sub.auto_renew)
        self.assertIsNone(self.sub.next_charge_at)
        self.assertTrue(has_club_access(self.user))
        self.assertIn('payment_method_detached', '\n'.join(logs.output))
        self.assertIn('subscription_auto_renew_disabled', '\n'.join(logs.output))
        self.assertNotIn('secret-old-token', '\n'.join(logs.output))
        self.assertTrue(detach_yookassa_payment_method(self.user).ok)
        self.assertFalse(resume_yookassa_subscription(self.user).ok)

    def test_detach_rolls_back_token_erasure_if_subscription_update_fails(self):
        with patch.object(ClubSubscription, 'save', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                detach_yookassa_payment_method(self.user)
        self.method.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(self.method.provider_payment_method_id, 'secret-old-token')
        self.assertTrue(self.method.is_active)
        self.assertTrue(self.sub.auto_renew)

    @patch('games.club_yookassa.Payment.create')
    def test_renewal_after_detach_including_stale_scheduler_selection_is_blocked(self, create):
        from games.club_yookassa import _renew_one
        detach_yookassa_payment_method(self.user)
        self.assertEqual(renew_due_subscriptions()['created'], 0)
        self.assertFalse(_renew_one(self.sub.pk, now=timezone.now()))
        create.assert_not_called()
        self.assertFalse(ClubYooKassaPayment.objects.exists())

    def test_endpoint_auth_csrf_post_and_ownership(self):
        self.assertEqual(self.client.post(self.url).status_code, 401)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        # Even supplied foreign identifiers cannot select somebody else's method.
        response = self.client.post(self.url, {'user_id': self.user.pk,
                                              'payment_method_id': 'secret-old-token'})
        self.assertEqual(response.status_code, 200)
        self.method.refresh_from_db()
        self.assertEqual(self.method.provider_payment_method_id, 'secret-old-token')
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url).status_code, 403)
        csrf_client.get(reverse('new_subscription'))
        response = csrf_client.post(self.url, HTTP_X_CSRFTOKEN=csrf_client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('secret-old-token', response.content.decode())

    @override_settings(CLUB_YOOKASSA_ENABLED=False, YOOKASSA_RECURRING_ENABLED=False)
    def test_detach_available_without_profile_or_feature_flags(self):
        self.user.profile.delete()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.method.refresh_from_db()
        self.assertIsNone(self.method.provider_payment_method_id)

    def test_cancel_keeps_visible_method_and_detach_remains_available_after_expiry(self):
        self.assertTrue(cancel_yookassa_subscription(self.user).ok)
        self.client.force_login(self.user)
        response = self.client.get(reverse('new_subscription'))
        self.assertContains(response, 'Банковская карта •••• 1234')
        self.assertContains(response, 'Отвязать карту?')
        self.assertNotContains(response, 'secret-old-token')
        self.sub.refresh_from_db()
        self.sub.paid_until = timezone.now() - timedelta(days=1)
        self.sub.save()
        self.assertContains(self.client.get(reverse('new_subscription')), 'Отвязать карту')
        self.client.post(self.url)
        response = self.client.get(reverse('new_subscription'))
        self.assertContains(response, 'Карта отвязана')
        self.assertContains(response, 'Автопродление отключено')
        self.assertNotContains(response, 'Возобновить автопродление')

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create')
    def test_resubscribe_requires_new_initial_success_and_does_not_restore_intro(self, create, _cfg):
        detach_yookassa_payment_method(self.user)
        self.sub.refresh_from_db()
        original_paid_until = self.sub.paid_until
        create.return_value = {'id': 'new-initial', 'status': 'pending',
                               'confirmation': {'confirmation_url': 'https://yookassa.test/new'}}
        result = start_monthly_subscription(self.user, return_url='https://interoves.com/subscription/')
        self.assertTrue(result.ok)
        self.assertEqual(result.payment.period_start, original_paid_until)
        self.assertEqual(result.payment.period_end, add_calendar_months(original_paid_until, 1))
        payload = create.call_args.args[0]
        self.assertEqual(payload['amount']['value'], '600.00')
        self.assertTrue(payload['save_payment_method'])
        self.assertEqual(payload['payment_method_data'], {'type': 'bank_card'})
        self.assertNotIn('payment_method_id', payload)
        self.assertFalse(SavedPaymentMethod.objects.filter(is_active=True).exists())
        data = _payment_payload(result.payment)
        data['payment_method']['id'] = 'new-token'
        data['payment_method']['card'] = {'last4': '5678', 'first6': '123456',
                                          'expiry_month': '10', 'expiry_year': '2030'}
        with self.assertLogs('games.club_yookassa') as logs, self.captureOnCommitCallbacks(execute=True):
            process_yookassa_club_payment_event('payment.succeeded', data)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.saved_payment_method.provider_payment_method_id, 'new-token')
        self.assertEqual(self.sub.saved_payment_method.card_last4, '5678')
        self.assertNotEqual(self.sub.saved_payment_method_id, self.method.pk)
        self.assertTrue(self.sub.auto_renew)
        self.assertEqual(self.sub.paid_until, add_calendar_months(original_paid_until, 1))
        self.assertEqual(initial_monthly_amount_kopecks(self.sub), AMOUNT_MONTHLY_KOPECKS)
        self.assertIn('payment_method_saved', '\n'.join(logs.output))
        self.assertNotIn('new-token', '\n'.join(logs.output))

    def _pending(self, kind='recurring_monthly'):
        return ClubYooKassaPayment.objects.create(
            user=self.user, club_subscription=self.sub, kind=kind,
            period_key='pending-test', idempotency_key='pending-test', amount=60000,
            period_start=self.sub.paid_until, period_end=self.sub.paid_until + timedelta(days=30),
            submitted_at=timezone.now(),
        )

    def test_late_recurring_success_after_detach_extends_access_without_restoring_token(self):
        local = self._pending()
        result = detach_yookassa_payment_method(self.user)
        self.assertIn('ещё может завершиться', result.message)
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.paid_until, local.period_end)
        self.assertFalse(self.sub.auto_renew)
        self.assertIsNone(self.sub.saved_payment_method_id)
        self.assertIsNone(self.sub.next_charge_at)
        self.assertFalse(SavedPaymentMethod.objects.exclude(provider_payment_method_id=None).exists())

    def test_unresolved_charge_warning_survives_refresh_until_verified_outcome(self):
        local = self._pending()
        self.client.force_login(self.user)
        self.client.post(self.url)
        for _ in range(2):
            response = self.client.get(reverse('new_subscription'))
            self.assertContains(response, 'Отвязка не отменяет уже запущенный платёж.')
            self.assertNotContains(response, 'secret-old-token')
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        response = self.client.get(reverse('new_subscription'))
        self.assertNotContains(response, 'Отвязка не отменяет уже запущенный платёж.')
        self.assertContains(response, 'Карта отвязана')

    def test_late_recurring_success_after_cancel_does_not_reenable_renewal(self):
        local = self._pending()
        cancel_yookassa_subscription(self.user)
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.auto_renew)
        self.assertIsNone(self.sub.next_charge_at)
        self.assertEqual(self.sub.saved_payment_method_id, self.method.pk)

    def test_late_initial_success_after_detach_does_not_reattach(self):
        local = self._pending('initial_monthly')
        detach_yookassa_payment_method(self.user)
        process_yookassa_club_payment_event('payment.succeeded', _payment_payload(local))
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.auto_renew)
        self.assertIsNone(self.sub.saved_payment_method_id)

    def test_sdk_card_conversion_stores_only_valid_last4(self):
        from yookassa.domain.response import PaymentResponse
        local = self._pending('initial_monthly')
        data = _payment_payload(local)
        data['payment_method']['card'] = {
            'last4': '4321', 'first6': '123456', 'expiry_year': '2030', 'expiry_month': '01',
        }
        process_yookassa_club_payment_event('payment.succeeded', dict(PaymentResponse(data)))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.saved_payment_method.display_name, 'Банковская карта •••• 4321')
        fields = {field.name for field in SavedPaymentMethod._meta.fields}
        self.assertFalse(fields & {'first6', 'expiry_year', 'expiry_month', 'cvv', 'card_number'})

    def test_invalid_last4_is_not_stored(self):
        local = self._pending('initial_monthly')
        data = _payment_payload(local)
        data['payment_method']['card'] = {'last4': '12345678'}
        process_yookassa_club_payment_event('payment.succeeded', data)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.saved_payment_method.display_name, 'Банковская карта')

    def test_unconfirmed_or_unsaved_or_non_card_payment_cannot_bind(self):
        self.method.delete()
        self.sub.refresh_from_db()
        local = self._pending('initial_monthly')
        for changes in ({'status': 'pending'}, {'payment_method': {'id': 'x', 'saved': False, 'type': 'bank_card'}},
                        {'payment_method': {'id': 'x', 'saved': 'true', 'type': 'bank_card'}},
                        {'payment_method': {'id': 'x', 'saved': True, 'type': 'sbp'}}):
            with self.subTest(changes=changes):
                local.status = 'pending'
                local.save()
                self.sub.cancelled_at = None
                self.sub.save()
                data = _payment_payload(local)
                data.update(changes)
                process_yookassa_club_payment_event('payment.succeeded', data)
                self.assertFalse(SavedPaymentMethod.objects.filter(is_active=True).exists())

    @patch('games.club_yookassa.configure_yookassa_from_env')
    @patch('games.club_yookassa.Payment.create', side_effect=TimeoutError('secret-old-token'))
    def test_ambiguous_submission_is_retained_disclosed_and_never_retried(self, create, _cfg):
        with self.assertLogs('games.club_yookassa') as logs:
            renew_due_subscriptions()
        self.assertNotIn('secret-old-token', '\n'.join(logs.output))
        local = ClubYooKassaPayment.objects.get()
        self.assertEqual(local.status, 'pending')
        self.assertEqual(local.failure_code, 'submission_unknown')
        self.assertFalse(_submit_renewal(local.pk))
        self.assertIn('ещё может завершиться', detach_yookassa_payment_method(self.user).message)
        self.assertFalse(_submit_renewal(local.pk))
        local.refresh_from_db()
        self.assertEqual(local.status, 'pending')
        renew_due_subscriptions()
        self.assertEqual(create.call_count, 1)

    @patch('games.club_yookassa._create_yookassa_payment')
    def test_pending_provider_response_cannot_be_submitted_again(self, create):
        create.return_value = {'id': 'pending-provider-payment', 'status': 'pending'}
        self.assertEqual(renew_due_subscriptions()['created'], 1)
        local = ClubYooKassaPayment.objects.get()
        self.assertFalse(_submit_renewal(local.pk))
        create.assert_called_once()

    @patch('games.club_yookassa._create_yookassa_payment', side_effect=SystemExit)
    def test_crash_during_submission_leaves_durable_claim_and_cannot_replay(self, create):
        with self.assertRaises(SystemExit):
            renew_due_subscriptions()
        local = ClubYooKassaPayment.objects.get()
        self.assertEqual(local.failure_code, 'submission_unknown')
        self.assertFalse(_submit_renewal(local.pk))
        self.assertIn('ещё может завершиться', detach_yookassa_payment_method(self.user).message)
        create.assert_called_once()


@override_settings(**YK_SETTINGS)
class ClubYooKassaWebhookTicketIsolationTests(TestCase):
    """Club webhook branch must not break ticket path when purpose missing."""

    def test_ticket_metadata_still_required_path(self):
        client = Client()
        with patch('games.views.ticket.configure_yookassa_from_env'), patch(
            'games.views.ticket.Payment.find_one'
        ) as find_mock:
            find_mock.return_value = {
                'id': 'pay-ticket',
                'status': 'succeeded',
                'metadata': {},
                'description': 'tickets',
            }
            response = client.post(
                '/yookassa/webhook/',
                data=json.dumps({
                    'event': 'payment.succeeded',
                    'object': {'id': 'pay-ticket'},
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)


class ClubYooKassaHttpTests(SimpleTestCase):
    @patch('games.club_yookassa_client.requests.Session')
    def test_http_wait_is_bounded_and_session_is_closed(self, session_factory):
        from games.club_yookassa_client import ClubApiClient
        with patch('yookassa.Configuration.account_id', 'test-shop'), \
                patch('yookassa.Configuration.secret_key', 'test-secret'):
            client = ClubApiClient()
        client.execute({}, 'POST', '/payments', None, {})
        session_factory.return_value.__enter__.return_value.request.assert_called_once_with(
            'POST', client.endpoint + '/payments', params=None, headers={}, json={},
            timeout=(3.05, 10),
        )
        session_factory.return_value.__exit__.assert_called_once()
