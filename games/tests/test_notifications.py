from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from games.community import friends
from games.models import BugReport, BugReportMessage, Profile
from games.notifications import service, telegram
from games.notifications.models import Notification, NotificationPrefs
from games.notifications.prefs import channels, outside_quiet_hours, set_channels, user_timezone
from games.ops.telegram.transport.api import SEND_BLOCKED, SEND_OK

MSK = ZoneInfo('Europe/Moscow')
SEND = 'games.ops.telegram.transport.api.send_message_status'
ENQUEUE = 'games.ops.telegram.bot.alerts.enqueue_integrations_message'


class NotificationFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user('nt_anna')
        cls.vera = User.objects.create_user('nt_vera')
        cls.boris = User.objects.create_user('nt_boris')
        cls.anna_p = Profile.objects.create(user=cls.anna, first_name='Анна', last_name='Петрова')
        cls.vera_p = Profile.objects.create(
            user=cls.vera, first_name='Вера', last_name='Зорина',
            telegram_user_id=222, telegram_verified=True,
        )
        cls.boris_p = Profile.objects.create(user=cls.boris, first_name='Борис', last_name='Лапин')

    def setUp(self):
        cache.clear()
        # Daytime in Moscow so quiet hours stay out of the way by default.
        self.noon = datetime(2026, 11, 3, 12, 0, tzinfo=MSK)

    def request(self, sender, recipient, *, enqueue=True):
        with patch(ENQUEUE, return_value=enqueue) as enqueue_mock, patch.object(timezone, 'now', return_value=self.noon):
            with self.captureOnCommitCallbacks(execute=True):
                friends.request_friendship(sender, recipient.pk)
        return enqueue_mock


class NotifyTests(NotificationFixture):
    def test_friend_request_lands_in_inbox_and_is_scheduled_for_telegram(self):
        enqueue = self.request(self.anna, self.vera)
        row = Notification.objects.get(user=self.vera)
        self.assertEqual((row.kind, row.actor_id, row.tg_state), ('friend.request', self.anna.pk, Notification.TG_PENDING))
        self.assertEqual(row.tg_due_at, self.noon + timedelta(minutes=2))
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs['payload'], {'kind': 'notification', 'notification_id': row.pk})
        self.assertEqual(kwargs['delay_seconds'], 120)
        row.refresh_from_db()
        self.assertIsNotNone(row.tg_enqueued_at)
        items = service.inbox(self.vera)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], 'Анна Петрова хочет добавить вас в друзья')

    def test_without_telegram_only_the_inbox(self):
        enqueue = self.request(self.anna, self.boris)
        row = Notification.objects.get(user=self.boris)
        self.assertEqual(row.tg_state, Notification.TG_NONE)
        enqueue.assert_not_called()

    def test_re_request_folds_into_the_unread_row(self):
        self.request(self.anna, self.vera)
        friends.remove(self.anna, self.vera.pk)
        enqueue = self.request(self.anna, self.vera)
        self.assertEqual(Notification.objects.filter(user=self.vera).count(), 1)
        # Already pending: folding does not enqueue a second copy.
        enqueue.assert_not_called()

    def test_answered_request_disappears_from_inbox(self):
        self.request(self.anna, self.vera)
        friends.accept_request(self.vera, self.anna.pk)
        self.assertEqual(service.inbox(self.vera), [])

    def test_site_off_keeps_telegram_but_hides_the_row(self):
        set_channels(self.vera.pk, 'friends', site=False, telegram=True)
        self.request(self.anna, self.vera)
        row = Notification.objects.get(user=self.vera)
        self.assertFalse(row.in_inbox)
        self.assertEqual(row.tg_state, Notification.TG_PENDING)
        self.assertEqual(service.inbox(self.vera), [])
        self.assertEqual(service.badge_count(self.vera.pk), 0)

    def test_both_channels_off_records_nothing(self):
        set_channels(self.vera.pk, 'friends', site=False, telegram=False)
        self.request(self.anna, self.vera)
        self.assertFalse(Notification.objects.filter(user=self.vera).exists())

    def test_mandatory_channels_cannot_be_switched_off(self):
        set_channels(self.vera.pk, 'club', site=False, telegram=False)
        self.assertEqual(channels(self.vera.pk, 'club'), (True, True))
        set_channels(self.vera.pk, 'support', site=False, telegram=False)
        self.assertEqual(channels(self.vera.pk, 'support'), (True, False))

    def test_enqueue_failure_leaves_row_for_the_sweep(self):
        self.request(self.anna, self.vera, enqueue=False)
        row = Notification.objects.get(user=self.vera)
        self.assertEqual(row.tg_state, Notification.TG_PENDING)
        self.assertIsNone(row.tg_enqueued_at)

    def test_failure_never_breaks_the_caller(self):
        with patch('games.notifications.service._notify', side_effect=RuntimeError('db down')):
            self.assertIsNone(service.notify(self.vera, 'friend.request', actor=self.anna, from_user_id=self.anna.pk))

    def test_badge_counts_only_after_last_seen(self):
        self.request(self.anna, self.vera)
        self.assertEqual(service.badge_count(self.vera.pk), 1)
        service.mark_seen(self.vera)
        self.assertEqual(service.badge_count(self.vera.pk), 0)
        # Still unread until opened.
        self.assertTrue(service.inbox(self.vera)[0]['unread'])

    def test_opening_friends_page_marks_requests_read(self):
        self.request(self.anna, self.vera)
        self.assertEqual(service.mark_read_for_page(self.vera, 'new_friends'), 1)
        self.assertFalse(service.inbox(self.vera)[0]['unread'])
        self.assertEqual(service.mark_read_for_page(self.vera, 'new_main'), 0)


class DeliveryTests(NotificationFixture):
    def pending(self):
        self.request(self.anna, self.vera)
        return Notification.objects.get(user=self.vera)

    @override_settings(SITE_BASE_URL='https://interoves.com')
    def test_delivers_once(self):
        row = self.pending()
        with patch(SEND, return_value=SEND_OK) as send:
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at), {'sent': True, 'reason': ''})
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'not_pending')
        send.assert_called_once()
        chat_id, text = send.call_args.args
        self.assertEqual(chat_id, 222)
        self.assertIn('Анна Петрова', text)
        row.refresh_from_db()
        self.assertEqual(row.tg_state, Notification.TG_SENT)

    def test_not_sent_before_due(self):
        row = self.pending()
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(row.pk, now=self.noon)['reason'], 'not_pending')
        send.assert_not_called()

    def test_skipped_when_read_on_site(self):
        row = self.pending()
        service.mark_read(self.vera)
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'read')
        send.assert_not_called()

    def test_skipped_when_request_answered(self):
        row = self.pending()
        friends.remove(self.vera, self.anna.pk)
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'not_valid')
        send.assert_not_called()

    def test_skipped_when_telegram_switched_off_meanwhile(self):
        row = self.pending()
        set_channels(self.vera.pk, 'friends', site=True, telegram=False)
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'disabled')
        send.assert_not_called()

    def test_cooldown_per_pair(self):
        row = self.pending()
        with patch(SEND, return_value=SEND_OK):
            telegram.deliver(row.pk, now=row.tg_due_at)
        service.mark_read(self.vera)
        friends.remove(self.anna, self.vera.pk)
        self.noon += timedelta(days=1)
        self.request(self.anna, self.vera)
        second = Notification.objects.filter(user=self.vera).exclude(pk=row.pk).get()
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(second.pk, now=second.tg_due_at)['reason'], 'cooldown')
        send.assert_not_called()

    def test_blocked_bot_is_remembered_and_cleared_on_success(self):
        row = self.pending()
        with patch(SEND, return_value=SEND_BLOCKED):
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'blocked')
        self.assertIsNotNone(NotificationPrefs.objects.get(user=self.vera).telegram_blocked_at)
        Notification.objects.filter(pk=row.pk).update(tg_state=Notification.TG_PENDING)
        with patch(SEND, return_value=SEND_OK):
            telegram.deliver(row.pk, now=row.tg_due_at)
        self.assertIsNone(NotificationPrefs.objects.get(user=self.vera).telegram_blocked_at)

    def test_sweep_sends_unqueued_and_lost_rows(self):
        row = self.pending()
        later = row.tg_due_at + timedelta(minutes=1)
        with patch(SEND, return_value=SEND_OK) as send:
            # Enqueued a moment ago: the queue will bring it, the sweep waits.
            self.assertEqual(telegram.sweep(now=later)['sent'], 0)
            self.assertEqual(telegram.sweep(now=later + telegram.LOST_AFTER)['sent'], 1)
        send.assert_called_once()

    def test_worker_dispatches_notification_payload(self):
        from games.ops.telegram.bot.user_notices import run_user_notice_live

        row = self.pending()
        with patch(SEND, return_value=SEND_OK), patch.object(timezone, 'now', return_value=row.tg_due_at):
            result = run_user_notice_live(payload={'kind': 'notification', 'notification_id': row.pk})
        self.assertEqual(result, {'sent': True, 'reason': ''})


class QuietHoursTests(NotificationFixture):
    def test_interval_crossing_midnight(self):
        prefs = NotificationPrefs(user=self.vera)
        late = datetime(2026, 11, 3, 23, 30, tzinfo=MSK)
        early = datetime(2026, 11, 4, 2, 0, tzinfo=MSK)
        wake = datetime(2026, 11, 4, 9, 0, tzinfo=MSK)
        self.assertEqual(outside_quiet_hours(late, prefs, MSK), wake)
        self.assertEqual(outside_quiet_hours(early, prefs, MSK), wake)
        self.assertEqual(outside_quiet_hours(self.noon, prefs, MSK), self.noon)

    def test_custom_daytime_interval_and_off(self):
        prefs = NotificationPrefs(user=self.vera, quiet_start=time(13, 0), quiet_end=time(15, 0))
        at = datetime(2026, 11, 3, 14, 0, tzinfo=MSK)
        self.assertEqual(outside_quiet_hours(at, prefs, MSK), datetime(2026, 11, 3, 15, 0, tzinfo=MSK))
        prefs.quiet_hours_enabled = False
        self.assertEqual(outside_quiet_hours(at, prefs, MSK), at)

    def test_uses_profile_timezone(self):
        self.vera_p.timezone = 'Asia/Yerevan (UTC+04:00)'
        self.assertEqual(user_timezone(self.vera_p), ZoneInfo('Asia/Yerevan'))
        self.vera_p.timezone = 'Not/AZone'
        self.assertEqual(user_timezone(self.vera_p), MSK)

    def test_night_request_waits_for_the_morning(self):
        self.noon = datetime(2026, 11, 3, 23, 50, tzinfo=MSK)
        enqueue = self.request(self.anna, self.vera)
        row = Notification.objects.get(user=self.vera)
        self.assertEqual(row.tg_due_at, datetime(2026, 11, 4, 9, 0, tzinfo=MSK))
        # Too far for the SQS delay: the sweep sends it.
        enqueue.assert_not_called()
        with patch(SEND, return_value=SEND_OK) as send:
            self.assertEqual(telegram.sweep(now=row.tg_due_at)['sent'], 1)
        send.assert_called_once()

    def test_quiet_hours_changed_after_arming_defer_the_send(self):
        self.request(self.anna, self.vera)
        row = Notification.objects.get(user=self.vera)
        NotificationPrefs.objects.create(user=self.vera, quiet_start=time(11, 0), quiet_end=time(13, 0))
        with patch(SEND) as send:
            self.assertEqual(telegram.deliver(row.pk, now=row.tg_due_at)['reason'], 'deferred')
        send.assert_not_called()
        row.refresh_from_db()
        self.assertEqual((row.tg_state, row.tg_due_at), (Notification.TG_PENDING, datetime(2026, 11, 3, 13, 0, tzinfo=MSK)))


class SupportReplyTests(NotificationFixture):
    def test_admin_reply_notifies_each_time(self):
        from games.tests.test_feedback import _ensure_reference_rows, _make_playable_game

        _ensure_reference_rows()
        game, task = _make_playable_game('nt_game')
        report = BugReport.objects.create(game=game, task=task, user=self.vera, text='не работает')
        with patch(ENQUEUE, return_value=True) as enqueue, patch.object(timezone, 'now', return_value=self.noon):
            for text in ('Починили', 'Ещё раз проверьте'):
                # The post_save signal notifies after commit.
                with self.captureOnCommitCallbacks(execute=True):
                    message = BugReportMessage.objects.create(
                        report=report, author_role=BugReportMessage.ROLE_ADMIN, text=text,
                    )
        rows = Notification.objects.filter(user=self.vera, kind='support.reply')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().payload['message_id'], message.pk)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(enqueue.call_args.kwargs['delay_seconds'], 0)
        self.assertEqual(service.inbox(self.vera)[0]['body'], 'Ещё раз проверьте')


class InboxViewTests(NotificationFixture):
    def setUp(self):
        super().setUp()
        self.request(self.anna, self.vera)
        self.row = Notification.objects.get(user=self.vera)
        self.client.force_login(self.vera)

    def test_bell_shows_badge_and_dropdown_clears_it(self):
        from django.urls import reverse

        page = self.client.get(reverse('new_notifications'))
        self.assertContains(page, 'data-bell-count')
        hub = self.client.get(reverse('new_notification_settings'))
        self.assertEqual(hub.context['notifications_badge'], 0)  # the inbox page marked it seen
        NotificationPrefs.objects.filter(user=self.vera).update(seen_at=None)
        self.assertEqual(self.client.get(reverse('new_notification_settings')).context['notifications_badge'], 1)
        dropdown = self.client.get(reverse('new_notifications_dropdown'))
        self.assertContains(dropdown, 'Анна Петрова хочет добавить вас в друзья')
        self.assertContains(dropdown, 'Прочитать все')
        self.assertEqual(service.badge_count(self.vera.pk), 0)

    def test_open_marks_read_and_redirects(self):
        from django.urls import reverse

        response = self.client.get(reverse('new_notification_open', args=[self.row.pk]))
        self.assertRedirects(response, reverse('new_friends'), fetch_redirect_response=False)
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.read_at)

    def test_cannot_open_someone_elses(self):
        from django.urls import reverse

        self.client.force_login(self.boris)
        self.assertEqual(self.client.get(reverse('new_notification_open', args=[self.row.pk])).status_code, 404)

    def test_visiting_the_target_page_answers_it(self):
        from django.urls import reverse

        self.client.get(reverse('new_friends'))
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.read_at)

    def test_read_all(self):
        from django.urls import reverse

        response = self.client.post(reverse('new_notifications_read_all'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.json(), {'read': 1, 'badge': 0})
        self.assertEqual(service.inbox(self.vera)[0]['unread'], False)

    def test_group_filter(self):
        from django.urls import reverse

        self.assertContains(self.client.get(reverse('new_notifications') + '?group=friends'), 'хочет добавить')
        self.assertNotContains(self.client.get(reverse('new_notifications') + '?group=club'), 'хочет добавить')

    def test_anonymous_gets_no_bell(self):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        from django.urls import resolve

        from games.site.http.context import notifications_nav

        request = RequestFactory().get('/friends/')
        request.resolver_match = resolve('/friends/')
        request.user = AnonymousUser()
        self.assertEqual(notifications_nav(request), {})
        request.user = self.vera
        self.assertEqual(notifications_nav(request), {'notifications_enabled': True, 'notifications_badge': 0})
