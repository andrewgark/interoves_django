import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.club_access import has_club_access
from games.models import (
    ClubSubscription,
    ClubSubscriptionEvent,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    TributePurchase,
)
from games.telegram_linking import consume_link_token, create_link_token, user_has_telegram_link
from games.tribute_util import compute_webhook_signature


CLUB_SETTINGS = {
    'CLUB_SUBSCRIPTION_ENABLED': True,
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
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_AMOUNT': '75000',
    'TRIBUTE_CLUB_SUBSCRIPTION_RUB_CURRENCY': 'RUB',
    'TRIBUTE_CLUB_SUBSCRIPTION_USD_ID': '9002',
    'TRIBUTE_CLUB_SUBSCRIPTION_USD_URL': 'https://t.me/tribute?start=club-usd',
    'TRIBUTE_CLUB_SUBSCRIPTION_USD_AMOUNT': '900',
    'TRIBUTE_CLUB_SUBSCRIPTION_USD_CURRENCY': 'USD',
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
        self.assertIn('Войти и оформить подписку', body)
        self.assertIn('750 ₽ в месяц', body)
        self.assertIn('$9 в месяц', body)
        self.assertIn('noindex,nofollow', body)
        self.assertIn('data-login-open', body)
        self.assertNotIn('Оформить вторую', body)

    def test_login_modal_returns_to_subscription(self):
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('/subscription/', body)
        self.assertIn('data-login-open', body)

    def test_linked_user_sees_checkout(self):
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Оформить', body)
        self.assertNotIn('Привязать Telegram', body)
        self.assertIn('value="rub"', body)
        self.assertIn('value="usd"', body)

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
            amount=75000,
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

    def test_usd_active_page_shows_dollar_price_not_checkout(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            currency='USD',
            amount=900,
            paid_until=timezone.now() + timedelta(days=20),
            tribute_subscription_id=9002,
            telegram_user_id=424242,
        )
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Подписка активна', body)
        self.assertIn('$9 в месяц', body)
        self.assertIn('Следующее списание', body)
        self.assertNotIn('value="rub"', body)

    def test_expired_page_shows_checkout_again(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_EXPIRED,
            auto_renew=False,
            currency='RUB',
            amount=75000,
            paid_until=timezone.now() - timedelta(days=1),
            tribute_subscription_id=9001,
            telegram_user_id=424242,
        )
        self.client.force_login(self.user)
        body = self.client.get(reverse('new_subscription')).content.decode()
        self.assertIn('Оплаченный период закончился', body)
        self.assertIn('value="rub"', body)
        self.assertIn('value="usd"', body)
        self.assertNotIn('Следующее списание', body)

    def test_checkout_requires_csrf_and_telegram(self):
        self.client.force_login(self.unlinked)
        response = self.client.post(reverse('new_subscription_checkout'), {'currency': 'rub'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'telegram_unlinked')


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
            'price': 75000,
            'amount': 75000,
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
        self.assertEqual(sub.amount, 75000)
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.telegram_user_id, 777001)
        self.assertTrue(user_has_telegram_link(self.user))

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

    def test_unknown_product_is_ignored(self):
        response = self._post(self._payload(subscription_id=5555))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_club_access(self.user))
        self.assertEqual(
            ClubSubscriptionEvent.objects.get().result,
            ClubSubscriptionEvent.RESULT_IGNORED_PRODUCT,
        )

    def test_usd_and_rub_give_the_same_entitlement(self):
        self._post(self._payload())
        ClubSubscription.objects.filter(user=self.user).update(
            paid_until=timezone.now() - timedelta(days=1),
            auto_renew=False,
            status=ClubSubscription.STATUS_EXPIRED,
        )
        usd = self._payload(
            subscription_id=9002,
            amount=900,
            price=900,
            currency='usd',
            period_id=22,
        )
        response = self._post(usd, created_at='2026-09-06T12:00:00Z')
        self.assertEqual(response.status_code, 200)
        sub = ClubSubscription.objects.get(user=self.user)
        self.assertEqual(sub.currency, 'USD')
        self.assertEqual(sub.amount, 900)
        self.assertTrue(has_club_access(self.user))

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
            self._payload(subscription_id=9002, amount=900, price=900, currency='usd', period_id=99),
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
        cls.archive_number = 1 if cls.today != 1 else 2
        for number in (cls.archive_number, cls.today):
            tg = TaskGroup.objects.create(label='club-archive-{}'.format(number))
            GameTaskGroup.objects.create(
                game=game, task_group=tg, number=str(number), name='#{}'.format(number),
            )
            Task.objects.create(
                task_group=tg, number='1', task_type='default', checker_data='ok',
            )
        cls.archive_url = '/ladder/{}/'.format(cls.archive_number)
        cls.today_url = '/ladder/{}/'.format(cls.today)
        cls.archive_task = Task.objects.get(
            task_group__game_links__game=game,
            task_group__game_links__number=str(cls.archive_number),
        )

    def test_anonymous_cannot_open_archive_url(self):
        response = self.client.get(self.archive_url)
        self.assertEqual(response.status_code, 403)
        self.assertIn('клубной подпиской', response.content.decode())

    def test_today_stays_free(self):
        response = self.client.get(self.today_url)
        self.assertEqual(response.status_code, 200)

    def test_cancelled_but_paid_opens_archive(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_CANCELLED,
            auto_renew=False,
            paid_until=timezone.now() + timedelta(days=5),
            currency='RUB',
            amount=75000,
        )
        self.client.force_login(self.user)
        self.assertTrue(has_club_access(self.user))
        response = self.client.get(self.archive_url)
        self.assertEqual(response.status_code, 200)

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

    def test_active_opens_archive(self):
        ClubSubscription.objects.create(
            user=self.user,
            status=ClubSubscription.STATUS_ACTIVE,
            auto_renew=True,
            paid_until=timezone.now() + timedelta(days=5),
            currency='USD',
            amount=900,
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
