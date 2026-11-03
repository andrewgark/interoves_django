from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from games.models import Game, GameTaskGroup, PlayerStartedGame, Profile, TaskGroup
from games.notifications import daily
from games.notifications.models import DailyReminder, NotificationPrefs
from games.notifications.prefs import channels
from games.ops.social.models import SocialQueuePost
from games.ops.telegram.transport.api import SEND_BLOCKED, SEND_OK

MSK = ZoneInfo('Europe/Moscow')
TEXT = 'games.ops.telegram.transport.api.send_message_status'
PHOTO = 'games.ops.telegram.transport.api.send_photo_status'
START = date(2026, 11, 1)  # edition 1; 2026-11-03 is edition 3


class DailyFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        from games.tests.test_feedback import _ensure_reference_rows

        _ensure_reference_rows()
        cls.user = User.objects.create_user('nd_vera')
        cls.profile = Profile.objects.create(
            user=cls.user, first_name='Вера', last_name='Зорина',
            telegram_user_id=333, telegram_verified=True,
        )
        cls.games = {}
        for game_id, tag in (('salad', 'word_salad_publish_start'), ('ladder', 'ladder_publish_start')):
            game, _ = Game.objects.get_or_create(
                id=game_id,
                defaults={'name': game_id, 'author': 't', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
            )
            game.tags = {tag: START.isoformat()}
            game.save()
            cls.games[game_id] = game
            for number in (1, 2, 3, 4):
                task_group = TaskGroup.objects.create(label='{}-{}'.format(game_id, number))
                GameTaskGroup.objects.create(game=game, task_group=task_group, number=str(number), name=str(number))

    def setUp(self):
        cache.clear()

    def at(self, hour, minute=0, day=3):
        return datetime(2026, 11, day, hour, minute, tzinfo=MSK)


@override_settings(SITE_BASE_URL='https://interoves.com')
class ScheduleTests(DailyFixture):
    def test_channel_release_and_custom_times(self):
        reminder = daily.set_reminder(self.user, 'salad', 'channel', now=self.at(10))
        self.assertEqual(reminder.next_send_at, self.at(14, 30))
        reminder = daily.set_reminder(self.user, 'salad', 'channel', now=self.at(15))
        self.assertEqual(reminder.next_send_at, self.at(14, 30, day=4))
        reminder = daily.set_reminder(self.user, 'salad', 'release', now=self.at(10))
        self.assertEqual(reminder.next_send_at, self.at(0, 20, day=4))
        self.profile.timezone = 'Asia/Yerevan'
        self.profile.save()
        self.user.refresh_from_db()
        reminder = daily.set_reminder(self.user, 'salad', 'custom', custom_time=time(9, 0), now=self.at(6))
        self.assertEqual(reminder.next_send_at, datetime(2026, 11, 3, 9, 0, tzinfo=ZoneInfo('Asia/Yerevan')))

    def test_alphabetty_channel_default_is_12_30(self):
        self.assertEqual(daily.channel_clock('alphabetty'), time(12, 30))

    def test_off_removes_and_custom_needs_time(self):
        daily.set_reminder(self.user, 'salad', 'channel')
        daily.set_reminder(self.user, 'salad', None)
        self.assertFalse(DailyReminder.objects.exists())
        with self.assertRaises(ValueError):
            daily.set_reminder(self.user, 'salad', 'custom')
        with self.assertRaises(ValueError):
            daily.set_reminder(self.user, 'week_task', 'channel')

    def test_reschedule_follows_timezone(self):
        daily.set_reminder(self.user, 'salad', 'custom', custom_time=time(9, 0), now=self.at(6))
        self.profile.timezone = 'Asia/Vladivostok'
        self.profile.save()
        self.user.refresh_from_db()
        daily.reschedule(self.user, now=self.at(6))
        expected = datetime(2026, 11, 4, 9, 0, tzinfo=ZoneInfo('Asia/Vladivostok'))
        self.assertEqual(DailyReminder.objects.get().next_send_at, expected)


@override_settings(SITE_BASE_URL='https://interoves.com')
class SweepTests(DailyFixture):
    def remind(self, game_id='salad', **kwargs):
        reminder = daily.set_reminder(self.user, game_id, 'channel', now=self.at(10), **kwargs)
        return reminder.next_send_at

    def test_sends_text_once_per_edition(self):
        due = self.remind()
        with patch(TEXT, return_value=SEND_OK) as send:
            self.assertEqual(daily.sweep(now=due), {'sent': 1, 'skipped': 0})
            # Same edition: moved to tomorrow, and even a forced re-run skips it.
            DailyReminder.objects.update(next_send_at=due)
            daily.sweep(now=due + timedelta(minutes=1))
        send.assert_called_once()
        chat_id, text = send.call_args.args
        self.assertEqual(chat_id, 333)
        self.assertIn('Салатик №3', text)
        keyboard = send.call_args.kwargs['reply_markup']['inline_keyboard']
        self.assertEqual(keyboard[0][0]['url'], 'https://interoves.com/salad/3/')
        self.assertIn(reverse('new_notification_settings'), keyboard[-1][0]['url'])
        reminder = DailyReminder.objects.get()
        self.assertEqual(reminder.last_sent_number, 3)
        self.assertEqual(reminder.next_send_at, self.at(14, 30, day=4))

    def test_not_due_yet(self):
        due = self.remind()
        with patch(TEXT) as send:
            self.assertEqual(daily.sweep(now=due - timedelta(minutes=1)), {'sent': 0, 'skipped': 0})
        send.assert_not_called()

    def test_skips_if_already_started(self):
        due = self.remind()
        link = GameTaskGroup.objects.get(game=self.games['salad'], number='3')
        PlayerStartedGame.objects.create(
            user=self.user, game=self.games['salad'], task_group=link.task_group,
            game_kind='salad', game_instance_id='x',
        )
        with patch(TEXT) as send:
            self.assertEqual(daily.sweep(now=due)['sent'], 0)
        send.assert_not_called()

    def test_started_does_not_matter_when_opted_out(self):
        due = self.remind(skip_if_played=False)
        link = GameTaskGroup.objects.get(game=self.games['salad'], number='3')
        PlayerStartedGame.objects.create(
            user=self.user, game=self.games['salad'], task_group=link.task_group,
            game_kind='salad', game_instance_id='x',
        )
        with patch(TEXT, return_value=SEND_OK) as send:
            daily.sweep(now=due)
        send.assert_called_once()

    def test_reminders_due_together_go_as_one_message(self):
        for game_id in ('salad', 'ladder'):
            daily.set_reminder(self.user, game_id, 'custom', custom_time=time(9, 0), now=self.at(6))
        with patch(TEXT, return_value=SEND_OK) as send:
            self.assertEqual(daily.sweep(now=self.at(9))['sent'], 1)
        send.assert_called_once()
        text = send.call_args.args[1]
        self.assertIn('Салатик №3', text)
        self.assertIn('Лесенка №3', text)
        self.assertEqual(len(send.call_args.kwargs['reply_markup']['inline_keyboard']), 3)

    def test_stale_reminder_is_skipped_but_rescheduled(self):
        due = self.remind()
        with patch(TEXT) as send:
            self.assertEqual(daily.sweep(now=due + timedelta(hours=1))['skipped'], 1)
        send.assert_not_called()
        self.assertEqual(DailyReminder.objects.get().next_send_at, self.at(14, 30, day=4))

    def test_blocked_bot_is_recorded(self):
        due = self.remind()
        with patch(TEXT, return_value=SEND_BLOCKED):
            daily.sweep(now=due)
        self.assertIsNotNone(NotificationPrefs.objects.get(user=self.user).telegram_blocked_at)
        self.assertIsNone(DailyReminder.objects.get().last_sent_number)

    def test_uses_channel_post_photo_and_reuses_upload(self):
        post = SocialQueuePost.objects.create(
            source=SocialQueuePost.SOURCE_WORD_SALAD, ladder_date=date(2026, 11, 3), ladder_number=3,
            caption='🥗 <b>Салатик №3</b>',
        )
        post.image.save('salad-3.png', ContentFile(b'png-bytes'), save=True)
        other = User.objects.create_user('nd_boris')
        Profile.objects.create(user=other, first_name='Б', last_name='Л', telegram_user_id=444, telegram_verified=True)
        due = self.remind()
        daily.set_reminder(other, 'salad', 'channel', now=self.at(10))
        with patch(PHOTO, return_value=(SEND_OK, 'file-123')) as send:
            daily.sweep(now=due)
        self.assertEqual(send.call_count, 2)
        photos = sorted([call.args[1] for call in send.call_args_list], key=str)
        # First upload sends bytes, the second reuses Telegram's file_id.
        self.assertEqual(photos, [b'png-bytes', 'file-123'])
        self.assertEqual(send.call_args.kwargs['caption'], '🥗 <b>Салатик №3</b>')

    def test_unlinked_telegram_sends_nothing(self):
        due = self.remind()
        Profile.objects.filter(pk=self.profile.pk).update(telegram_verified=False)
        with patch(TEXT) as send:
            daily.sweep(now=due)
        send.assert_not_called()


@override_settings(SITE_BASE_URL='https://interoves.com')
class SettingsPageTests(DailyFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('new_notification_settings')

    def post(self, **data):
        payload = {'section': 'all', 'quiet_start': '23:00', 'quiet_end': '09:00', 'quiet_enabled': '1'}
        payload.update(data)
        return self.client.post(self.url, payload)

    def test_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Напоминания о ежедневках')
        self.assertContains(response, 'Как в канале — 14:30')
        self.assertContains(response, 'Как в канале — 12:30')  # alphabetty

    def test_saves_channels_quiet_hours_and_reminders(self):
        response = self.post(**{
            'site_friends': '1', 'tg_support': '1',
            'quiet_start': '22:30', 'quiet_end': '08:00',
            'daily_salad': 'custom', 'daily_time_salad': '07:45', 'daily_skip_salad': '1',
            'daily_ladder': 'channel',
        })
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(channels(self.user.pk, 'friends'), (True, False))
        self.assertEqual(channels(self.user.pk, 'offers'), (False, False))
        self.assertEqual(channels(self.user.pk, 'club'), (True, True))
        prefs = NotificationPrefs.objects.get(user=self.user)
        self.assertEqual((prefs.quiet_start, prefs.quiet_end), (time(22, 30), time(8, 0)))
        reminders = {row.game_id: row for row in DailyReminder.objects.filter(user=self.user)}
        self.assertEqual(set(reminders), {'salad', 'ladder'})
        self.assertEqual(reminders['salad'].custom_time, time(7, 45))
        self.assertTrue(reminders['salad'].skip_if_played)
        self.assertFalse(reminders['ladder'].skip_if_played)

    def test_custom_without_time_is_an_error(self):
        self.post(**{'daily_salad': 'custom', 'daily_time_salad': ''})
        self.assertFalse(DailyReminder.objects.exists())

    def test_without_telegram_keeps_telegram_settings(self):
        daily.set_reminder(self.user, 'salad', 'channel')
        Profile.objects.filter(pk=self.profile.pk).update(telegram_verified=False)
        self.post(**{'site_friends': '1'})
        self.assertEqual(channels(self.user.pk, 'friends'), (True, True))
        self.assertTrue(DailyReminder.objects.filter(user=self.user, game_id='salad').exists())

    def test_timezone_update(self):
        daily.set_reminder(self.user, 'salad', 'custom', custom_time=time(9, 0))
        self.client.post(self.url, {'section': 'timezone', 'timezone': 'Asia/Yerevan'})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'Asia/Yerevan')
        local = DailyReminder.objects.get().next_send_at.astimezone(ZoneInfo('Asia/Yerevan'))
        self.assertEqual(local.time(), time(9, 0))
        self.assertEqual(self.client.post(self.url, {'section': 'timezone', 'timezone': 'Mars/Base'}).status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
