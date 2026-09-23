import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.club_access import has_club_access, user_can_access_desyatka
from games.models import (
    ClubSubscription,
    ClubSubscriptionEvent,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    PlayerCompletedGame,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
    TributePurchase,
)
from games.telegram_linking import consume_link_token, create_link_token, user_has_telegram_link
from games.telegram.notify import notify_admin_club_subscription
from games.tribute_config import club_checkout_enabled, club_product_configuration
from games.tribute_util import compute_webhook_signature


CLUB_SETTINGS = {
    'CLUB_PAYMENTS_ENABLED': True,
    'CLUB_ARCHIVE_GATING_ENABLED': True,
    'CLUB_YOOKASSA_ENABLED': False,
    'YOOKASSA_RECURRING_ENABLED': False,
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
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_ID': '9001',
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_URL': 'https://web.tribute.tg/s/club-rub',
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_AMOUNT': '60000',
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_FIRST_AMOUNT': '42000',
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_CURRENCY': 'RUB',
    'TRIBUTE_CLUB_SUBSCRIPTION_EUR_ID': '262466',
    'TRIBUTE_CLUB_SUBSCRIPTION_EUR_URL': 'https://t.me/tribute/app?startapp=s16hk',
    'TRIBUTE_CLUB_SUBSCRIPTION_EUR_AMOUNT': '555',
    'TRIBUTE_CLUB_SUBSCRIPTION_EUR_FIRST_AMOUNT': '389',
    'TRIBUTE_CLUB_SUBSCRIPTION_EUR_CURRENCY': 'EUR',
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


@override_settings(**CLUB_SETTINGS)
class ClubSubscriptionPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('club-user', password='secret')
        Profile.objects.create(
            user=cls.user,
            first_name='Club',
            last_name='User',
            telegram_user_id=424242,
            telegram_username='clubber',
            telegram_verified=True,
            telegram_linked_at=timezone.now(),
        )
        cls.unlinked = User.objects.create_user('club-unlinked', password='secret')
        Profile.objects.create(user=cls.unlinked, first_name='No', last_name='Tg')

    def test_anonymous_sees_page_and_login_cta(self):
        response = self.client.get(reverse('new_subscription'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Клубная подписка', body)
        self.assertIn('Войти, чтобы оформить', body)
        self.assertIn('420 ₽', body)
        self.assertIn('600 ₽', body)
        self.assertIn('€5.55', body)
        self.assertIn('первый месяц', body)
        self.assertIn('€3.89', body)
        self.assertNotIn('12 месяцев', body)
        self.assertNotIn('за год', body)
        self.assertIn('последние 7 заданий', body)
        self.assertIn('noindex,nofollow', body)
        self.assertIn('data-login-open', body)
        self.assertNotIn('Оформить вторую', body)

    def test_eur_only_tribute_club_configuration_is_valid(self):
        eur_only = {
            key: value
            for key, value in CLUB_SETTINGS.items()
            if not key.startswith('TRIBUTE_CLUB_SUBSCRIPTION_RUB_')
        }
        eur_only.update({
            'TRIBUTE_CLUB_SUBSCRIPTION_RUB_ID': '',
            'TRIBUTE_CLUB_SUBSCRIPTION_RUB_URL': '',
        })
        with self.settings(**eur_only):
            products, errors = club_product_configuration()
            self.assertEqual(set(products), {'eur'})
            self.assertEqual(errors, [])
            self.assertTrue(club_checkout_enabled())

    def test_login_modal_returns_to_subscription(self):
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('/subscription/', body)
        self.assertIn('data-login-open', body)

    def test_linked_user_sees_checkout(self):
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Оформить', body)
        self.assertNotIn('Привязать Telegram', body)
        self.assertIn('value="eur"', body)

    def test_unlinked_user_reuses_telegram_link_flow(self):
        self.client.force_login(self.unlinked)
        response = self.client.get(reverse('new_subscription'))
        body = response.content.decode()
        self.assertIn('Привязать Telegram', body)
        self.assertIn('name="next"', body)
        self.assertIn('/subscription/?telegram=linked', body)
        post = self.client.post(reverse('new_telegram_link_start'), {
            'next': '/subscription/?telegram=linked',
        })
        self.assertEqual(post.status_code, 302)
        self.assertTrue(post['Location'].startswith('https://t.me/interoves_test_bot?start='))

    def test_active_subscriber_does_not_see_second_checkout(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            currency='RUB',
            amount=60000,
            paid_until=timezone.now() + timedelta(days=20),
            tribute_subscription_id=9001,
            telegram_user_id=424242,
        )
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Подписка активна', body)
        self.assertIn('Управлять подпиской', body)
        self.assertNotIn('value="rub"', body)
        self.assertIn('t.me/tribute', body)

    def test_eur_active_page_shows_euro_price_not_checkout(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            currency='EUR',
            amount=555,
            paid_until=timezone.now() + timedelta(days=20),
            tribute_subscription_id=262466,
            telegram_user_id=424242,
        )
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Подписка активна', body)
        self.assertIn('€5.55 в месяц', body)
        self.assertIn('Следующее списание', body)
        self.assertNotIn('value="rub"', body)

    def test_expired_page_shows_checkout_again(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_EXPIRED,
            auto_renew=False,
            currency='RUB',
            amount=60000,
            paid_until=timezone.now() - timedelta(days=1),
            tribute_subscription_id=9001,
            telegram_user_id=424242,
        )
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Оплаченный период закончился', body)
        self.assertIn('value="eur"', body)
        self.assertNotIn('Следующее списание', body)

    @patch('games.telegram.notify.notify_admin_club_subscription_attempt_failed')
    def test_checkout_requires_csrf_and_telegram(self, notify_mock):
        self.client.force_login(self.unlinked)
        response = self.client.post(reverse('new_subscription_checkout'), {'currency': 'rub'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'telegram_unlinked')
        notify_mock.assert_called_once()

    @patch('games.telegram.notify.notify_admin_club_subscription_attempt_failed')
    def test_failed_tribute_checkout_notifies_admin(self, notify_mock):
        self.client.force_login(self.user)
        response = self.client.post(reverse('new_subscription_checkout'), {'currency': 'rub'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reason'], 'use_yookassa')
        notify_mock.assert_called_once()
        self.assertEqual(notify_mock.call_args.kwargs['provider'], 'tribute')
        self.assertEqual(notify_mock.call_args.kwargs['reason'], 'use_yookassa')


@override_settings(**CLUB_SETTINGS)
class ClubWebhookAndMappingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('club-wh', password='secret')
        Profile.objects.create(
            user=cls.user,
            first_name='Hook',
            last_name='User',
            telegram_user_id=777001,
            telegram_username='clubhook',
            telegram_verified=True,
            telegram_linked_at=timezone.now(),
        )

    def setUp(self):
        self.http = Client()

    def _payload(self, **overrides):
        payload = {
            'subscription_name': 'Клубная подписка',
            'subscription_id': 9001,
            'period_id': 11,
            'period': 'monthly',
            'type': 'regular',
            'price': 60000,
            'amount': 60000,
            'currency': 'rub',
            'trb_user_id': 'T-1',
            'telegram_user_id': 777001,
            'telegram_username': 'clubhook',
            'expires_at': (timezone.now() + timedelta(days=30)).isoformat().replace('+00:00', 'Z'),
        }
        payload.update(overrides)
        return payload

    def _post(self, payload, *, event='new_subscription', signature=True, created_at='2026-09-05T12:00:00Z'):
        body = json.dumps({
            'name': event,
            'created_at': created_at,
            'sent_at': created_at,
            'payload': payload,
        }).encode()
        sig = compute_webhook_signature(body, CLUB_SETTINGS['TRIBUTE_API_KEY']) if signature else 'invalid'
        return self.http.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=sig,
        )

    def test_valid_signature_new_subscription_grants_access(self):
        response = self._post(self._payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(has_club_access(self.user))
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.status, ClubSubscription.STATUS_ACTIVE)
        self.assertEqual(sub.currency, 'RUB')
        self.assertEqual(sub.amount, 60000)
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.telegram_user_id, 777001)
        self.assertTrue(user_has_telegram_link(self.user))

    @patch('games.telegram.notify.send_admin_message', return_value=True)
    def test_admin_notification_formats_eur_amount_in_major_units(self, send_admin):
        subscription = ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            currency='EUR',
            amount=389,
            paid_until=timezone.now() + timedelta(days=30),
        )

        self.assertTrue(notify_admin_club_subscription(subscription.pk, 'new_subscription'))
        message = send_admin.call_args.args[0]
        self.assertIn('Сумма: 3.89 EUR', message)
        self.assertNotIn('Сумма: 389 EUR', message)

    @patch('games.telegram.notify.send_admin_message', return_value=True)
    def test_admin_notification_labels_recurring_payment_as_renewal(self, send_admin):
        subscription = ClubSubscription.objects.create(
            user=self.user,
            provider=ClubSubscription.PROVIDER_YOOKASSA,
            status=ClubSubscription.STATUS_ACTIVE,
            currency='RUB',
            amount=60000,
            paid_until=timezone.now() + timedelta(days=30),
        )

        self.assertTrue(notify_admin_club_subscription(
            subscription.pk, 'payment.succeeded', payment_kind='recurring_monthly',
        ))
        self.assertIn('Продление клубной подписки', send_admin.call_args.args[0])

    def test_invalid_signature_is_rejected(self):
        response = self._post(self._payload(), signature=False)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(has_club_access(self.user))

    def test_unknown_telegram_id_does_not_grant_access(self):
        response = self._post(self._payload(telegram_user_id=999999, telegram_username='clubhook'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_club_access(self.user))
        event = ClubSubscriptionEvent.objects.get()
        self.assertEqual(event.result, ClubSubscriptionEvent.RESULT_UNMATCHED_TELEGRAM)

    def test_username_is_not_used_as_identity(self):
        other = User.objects.create_user('other-club')
        Profile.objects.create(
            user=other, first_name='O', last_name='T',
            telegram_user_id=111, telegram_username='clubhook', telegram_verified=True,
        )
        self._post(self._payload(telegram_user_id=777001, telegram_username='someone_else'))
        self.assertTrue(has_club_access(self.user))
        self.assertFalse(has_club_access(other))

    def test_tribute_id_can_match_stored_oidc_subject_during_migration(self):
        legacy_user = User.objects.create_user('legacy-oidc-club')
        Profile.objects.create(
            user=legacy_user,
            first_name='Legacy',
            last_name='OIDC',
            telegram_user_id=888003,
            telegram_oidc_sub='777002',
            telegram_username='legacyclub',
            telegram_verified=True,
        )
        response = self._post(self._payload(telegram_user_id=777002))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(has_club_access(legacy_user))

    def test_unknown_product_is_ignored(self):
        response = self._post(self._payload(subscription_id=5555))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_club_access(self.user))
        self.assertEqual(
            ClubSubscriptionEvent.objects.get().result,
            ClubSubscriptionEvent.RESULT_IGNORED_PRODUCT,
        )

    def test_eur_and_rub_give_the_same_entitlement(self):
        self._post(self._payload())
        ClubSubscription.objects.filter(user=self.user).update(
            paid_until=timezone.now() - timedelta(days=1),
            auto_renew=False,
            status=ClubSubscription.STATUS_EXPIRED,
        )
        eur = self._payload(
            subscription_id=262466,
            amount=555,
            price=555,
            currency='eur',
            period_id=22,
        )
        response = self._post(eur, created_at='2026-09-06T12:00:00Z')
        self.assertEqual(response.status_code, 200)
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.currency, 'EUR')
        self.assertEqual(sub.amount, 555)
        self.assertTrue(has_club_access(self.user))

    def test_eur_first_month_is_accepted_but_yearly_amount_is_rejected(self):
        first = self._payload(
            subscription_id=262466,
            amount=389,
            price=389,
            currency='eur',
            period_id=492551,
        )
        self.assertEqual(self._post(first).status_code, 200)
        self.assertTrue(has_club_access(self.user))
        ClubSubscription.objects.filter(user=self.user).update(
            paid_until=timezone.now() - timedelta(days=1),
            auto_renew=False,
            status=ClubSubscription.STATUS_EXPIRED,
        )
        yearly = self._payload(
            subscription_id=262466,
            amount=5800,
            price=5800,
            currency='eur',
            period_id=492550,
            expires_at=(timezone.now() + timedelta(days=365)).isoformat().replace('+00:00', 'Z'),
        )
        self.assertEqual(self._post(yearly, created_at='2026-09-06T12:00:00Z').status_code, 200)
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertNotEqual(sub.amount, 5800)
        self.assertFalse(has_club_access(self.user))

    def test_renewal_extends_paid_until_and_is_idempotent(self):
        first_end = timezone.now() + timedelta(days=30)
        self._post(self._payload(expires_at=first_end.isoformat().replace('+00:00', 'Z')))
        later = first_end + timedelta(days=31)
        payload = self._payload(
            period_id=12,
            expires_at=later.isoformat().replace('+00:00', 'Z'),
        )
        self._post(payload, event='renewed_subscription', created_at='2026-10-05T12:00:00Z')
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertGreater(sub.paid_until, first_end)
        first_renewal_count = ClubSubscriptionEvent.objects.count()
        self._post(payload, event='renewed_subscription', created_at='2026-10-05T12:00:00Z')
        self.assertEqual(ClubSubscriptionEvent.objects.count(), first_renewal_count)
        sub.refresh_from_db()
        self.assertGreater(sub.paid_until, first_end)

    def test_cancellation_keeps_paid_access(self):
        end = timezone.now() + timedelta(days=10)
        self._post(self._payload(expires_at=end.isoformat().replace('+00:00', 'Z')))
        self._post(
            self._payload(expires_at=end.isoformat().replace('+00:00', 'Z')),
            event='cancelled_subscription',
            created_at='2026-09-06T12:00:00Z',
        )
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.status, ClubSubscription.STATUS_CANCELLED)
        self.assertFalse(sub.auto_renew)
        self.assertTrue(has_club_access(self.user))
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Автопродление отключено', body)
        self.assertNotIn('Следующее списание', body)

    def test_expired_subscription_closes_access(self):
        self._post(self._payload(expires_at=(timezone.now() - timedelta(days=1)).isoformat().replace('+00:00', 'Z')))
        self.assertFalse(has_club_access(self.user))

    def test_malformed_payload_returns_400(self):
        response = self._post({'subscription_id': 'nope'})
        self.assertEqual(response.status_code, 400)

    def test_one_time_digital_product_webhook_still_works(self):
        from games.models import Team, TributePaymentIntent, TicketRequest
        from games.tribute_service import create_or_reuse_intent

        team = Team.objects.create(name='club_ticket_team', project_id='main', tickets=0, ticket_price=2000)
        self.user.profile.add_team_membership(team, make_primary=True)
        create_or_reuse_intent(user=self.user, team=team)
        purchase = {
            'product_id': 1001,
            'product_name': 'ticket',
            'amount': 1500,
            'currency': 'eur',
            'trb_user_id': 'T-2',
            'telegram_user_id': 777001,
            'telegram_username': 'clubhook',
            'purchase_id': 'dp-1',
            'transaction_id': 'tx-1',
            'purchase_created_at': '2026-09-05T12:00:00Z',
        }
        body = json.dumps({
            'name': 'new_digital_product',
            'created_at': '2026-09-05T12:00:01Z',
            'payload': purchase,
        }).encode()
        sig = compute_webhook_signature(body, CLUB_SETTINGS['TRIBUTE_API_KEY'])
        response = self.http.post(
            '/tribute/webhook/', data=body, content_type='application/json', HTTP_TRBT_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TributePurchase.objects.get(purchase_id='dp-1').status, 'issued')
        team.refresh_from_db()
        self.assertEqual(team.tickets, 1)
        self.assertFalse(ClubSubscription.objects.filter(user=self.user).exists())

    def test_duplicate_paid_subscriptions_are_flagged(self):
        self._post(self._payload())
        self._post(
            self._payload(subscription_id=262466, amount=555, price=555, currency='eur', period_id=99),
            created_at='2026-09-06T12:00:00Z',
        )
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertTrue(sub.duplicate_detected)
        self.assertEqual(ClubSubscription.objects.filter(user=self.user).count(), 1)
        self.assertTrue(has_club_access(self.user))

    def test_delayed_webhook_does_not_overwrite_newer_state(self):
        end = timezone.now() + timedelta(days=20)
        self._post(
            self._payload(expires_at=end.isoformat().replace('+00:00', 'Z')),
            created_at='2026-09-06T12:00:00Z',
        )
        self._post(
            self._payload(expires_at=end.isoformat().replace('+00:00', 'Z')),
            event='cancelled_subscription',
            created_at='2026-09-07T12:00:00Z',
        )
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.status, ClubSubscription.STATUS_CANCELLED)
        self._post(
            self._payload(period_id=99, expires_at=(end + timedelta(days=30)).isoformat().replace('+00:00', 'Z')),
            event='renewed_subscription',
            created_at='2026-09-05T12:00:00Z',
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, ClubSubscription.STATUS_CANCELLED)
        self.assertFalse(sub.auto_renew)
        self.assertEqual(
            ClubSubscriptionEvent.objects.filter(result=ClubSubscriptionEvent.RESULT_DELAYED).count(),
            1,
        )

    def test_gift_subscription_type_is_ignored(self):
        response = self._post(self._payload(type='gift'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_club_access(self.user))
        self.assertEqual(
            ClubSubscriptionEvent.objects.get().result,
            ClubSubscriptionEvent.RESULT_IGNORED_TYPE,
        )

    def test_unmatched_webhook_retries_after_telegram_is_linked(self):
        payload = self._payload(telegram_user_id=888002)
        self._post(payload)
        self.assertFalse(has_club_access(self.user))
        Profile.objects.filter(pk=self.user.profile.pk).update(
            telegram_user_id=888002,
            telegram_verified=True,
        )
        self._post(payload)
        self.assertTrue(has_club_access(self.user))
        self.assertEqual(ClubSubscriptionEvent.objects.count(), 1)
        self.assertEqual(
            ClubSubscriptionEvent.objects.get().result,
            ClubSubscriptionEvent.RESULT_APPLIED,
        )

    def test_linking_telegram_applies_pending_club_webhook(self):
        late = User.objects.create_user('late-club-link', password='secret')
        Profile.objects.create(user=late, first_name='Late', last_name='Link')
        self._post(self._payload(telegram_user_id=888001))
        self.assertFalse(has_club_access(late))
        _token, raw = create_link_token(late, next_path='/subscription/?telegram=linked')
        consume_link_token(raw, telegram_user_id=888001, telegram_username='lateclub')
        self.assertTrue(has_club_access(late))

    def test_unix_expires_at_is_accepted(self):
        end = timezone.now() + timedelta(days=21)
        response = self._post(self._payload(expires_at=int(end.timestamp())))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(has_club_access(self.user))

    def test_expired_subscription_link_hint_mentions_subscription_page(self):
        late = User.objects.create_user('hint-user', password='secret')
        Profile.objects.create(user=late, first_name='H', last_name='U')
        token, raw = create_link_token(late, next_path='/subscription/?telegram=linked')
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=['expires_at'])
        from games.telegram_linking import TelegramLinkError
        with self.assertRaises(TelegramLinkError) as raised:
            consume_link_token(raw, telegram_user_id=1)
        self.assertIn('/subscription/', raised.exception.message)


@override_settings(**CLUB_SETTINGS)
class ClubArchiveAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.user = User.objects.create_user('archive-user', password='secret')
        Profile.objects.create(user=cls.user, first_name='A', last_name='U')
        game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={'name': 'Ladder', 'author': 't', 'project_id': 'sections'},
        )
        if game.project_id != 'sections':
            game.project_id = 'sections'
        game.tags = {'ladder_publish_start': '2026-07-08T00:00:00+03:00'}
        game.save()
        cls.game = game
        from games.daily_section import current_number_for

        cls.today = current_number_for(game) or 60
        cls.recent_free = max(1, int(cls.today) - 3)
        cls.archive_number = max(1, int(cls.today) - 20)
        assert cls.archive_number < int(cls.today) - 6, (
            'fixture needs an archive number older than the free window'
        )
        for number in {cls.archive_number, cls.recent_free, cls.today}:
            tg = TaskGroup.objects.create(label='club-archive-{}'.format(number))
            GameTaskGroup.objects.create(
                game=game, task_group=tg, number=str(number), name='#{}'.format(number),
            )
            Task.objects.create(
                task_group=tg, number='1', task_type='default', checker_data='ok',
            )
        cls.archive_url = '/ladder/{}/'.format(cls.archive_number)
        cls.recent_free_url = '/ladder/{}/'.format(cls.recent_free)
        cls.today_url = '/ladder/{}/'.format(cls.today)
        cls.archive_task = Task.objects.get(
            task_group__game_links__game=game,
            task_group__game_links__number=str(cls.archive_number),
        )

    def test_anonymous_cannot_open_archive_url(self):
        response = self.client.get(self.archive_url)
        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertIn('клубной подпиской', body)
        self.assertIn('последних 7', body)

    def test_today_stays_free(self):
        response = self.client.get(self.today_url)
        self.assertEqual(response.status_code, 200)

    def test_recent_seven_stay_free_without_subscription(self):
        response = self.client.get(self.recent_free_url)
        self.assertEqual(response.status_code, 200)

    def test_cancelled_but_paid_opens_archive(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_CANCELLED,
            auto_renew=False,
            paid_until=timezone.now() + timedelta(days=5),
            currency='RUB',
            amount=60000,
        )
        self.client.force_login(self.user)
        self.assertTrue(has_club_access(self.user))
        response = self.client.get(self.archive_url)
        self.assertEqual(response.status_code, 200)

    def test_fully_solved_archive_stays_open_without_subscription(self):
        PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.archive_task.task_group,
            game_kind='ladder',
            game_instance_id='solved-archive-1',
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.archive_url).status_code, 200)

    def test_expired_closes_archive_and_attempts(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_EXPIRED,
            auto_renew=False,
            paid_until=timezone.now() - timedelta(days=1),
        )
        self.client.force_login(self.user)
        self.assertFalse(has_club_access(self.user))
        self.assertEqual(self.client.get(self.archive_url).status_code, 403)
        attempt = self.client.post('/send_attempt/{}/'.format(self.archive_task.pk), {
            'text': 'answer',
        })
        self.assertEqual(attempt.json()['status'], 'no_access')

    def test_registered_desyatka_team_keeps_access_without_club_subscription(self):
        team = Team.objects.create(name='registered-desyatka-team', project_id='main')
        self.user.profile.add_team_membership(team, make_primary=True)
        game = Game.objects.create(
            id='des1', name='Десяточка 1', author='test', project_id='main',
        )
        Registration.objects.create(game=game, team=team)

        with patch('games.club_access._latest_desyatka_numbers', return_value={2, 3, 4, 5, 6, 7, 8}):
            self.assertFalse(has_club_access(self.user))
            self.assertTrue(user_can_access_desyatka(self.user, game))

    def test_club_subscriber_can_play_archived_desyatka_as_team(self):
        team = Team.objects.create(name='club-desyatka-team', project_id='main')
        self.user.profile.add_team_membership(team, make_primary=True)
        game = Game.objects.create(
            id='des9', name='Десяточка 9', author='test', project_id='main',
            is_ready=True, is_playable=True, is_tournament=True,
            is_registrable=False,
            start_time=timezone.now() - timedelta(days=30),
            end_time=timezone.now() - timedelta(days=29),
        )
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            paid_until=timezone.now() + timedelta(days=5),
        )

        with patch('games.club_access._latest_desyatka_numbers', return_value={10, 11, 12, 13, 14, 15, 16}):
            self.assertTrue(user_can_access_desyatka(self.user, game))
            self.assertTrue(game.has_access('play', team=team))
            self.assertTrue(game.has_access('play_with_team', team=team))

    def test_active_opens_archive(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            paid_until=timezone.now() + timedelta(days=5),
            currency='EUR',
            amount=555,
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.archive_url).status_code, 200)

    def test_live_state_archive_returns_club_required(self):
        self.client.force_login(self.user)
        response = self.client.get(
            '/ladder/live-state/?task_ids={}'.format(self.archive_task.pk),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['reason'], 'club_required')

    def test_locked_archive_page_is_noindex(self):
        body = self.client.get(self.archive_url).content.decode()
        self.assertEqual(self.client.get(self.archive_url).status_code, 403)
        self.assertIn('noindex,nofollow', body)
