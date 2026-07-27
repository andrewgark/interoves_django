from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from games.models import (
    Attempt, Game, GameTaskGroup, Hint, HintAttempt, HTMLPage, Project, Task, TaskGroup, Team,
)
from games.social.models import SocialQueuePost
from games.telegram.announcements import (
    build_podium,
    format_all_solved_announcement,
    format_game_day_before_announcement,
    format_game_hour_before_announcement,
    format_game_results_announcement,
    format_hints_taken_line,
)
from games.telegram.models import TelegramGameAnnouncement
from games.telegram.scheduling import process_game_announcements

_FAKE_PNG = b'\x89PNG\r\n\x1a\nxxxx'


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_ANNOUNCE_CHAT_IDS=['-100999'],
    SITE_BASE_URL='https://interoves.com',
    LANGUAGE_CODE='ru-ru',
    TELEGRAM_LADDER_SCREENSHOT=False,
)
class ChatLifecycleAnnouncementTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()
        self.game = Game.objects.create(
            id='life_game',
            name='Life Game',
            no_html_name='Life Game',
            author='author',
            game_url='https://docs.google.com/document/d/conditions',
            answers_url='https://docs.google.com/document/d/answers',
            tags={'telegram_announce': True},
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            start_time=self.now + timedelta(hours=2),
            end_time=self.now + timedelta(hours=5),
        )

    def _set_window(self, *, start_delta, end_delta):
        self.game.start_time = self.now + start_delta
        self.game.end_time = self.now + end_delta
        self.game.save(update_fields=['start_time', 'end_time'])

    @patch('games.telegram.scheduling.publish_instagram')
    @patch('games.telegram.scheduling.publish_twitter')
    @patch('games.telegram.scheduling.publish_telegram')
    @patch('games.telegram.scheduling._game_announce_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_day_before(self, _admin, announce_mock, _png, tg_pub, tw_pub, ig_pub):
        self._set_window(start_delta=timedelta(hours=20), end_delta=timedelta(hours=23))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['day_before'], 1)
        self.assertEqual(stats['hour_before'], 0)
        announce_mock.assert_not_called()
        tg_pub.assert_called_once()
        tw_pub.assert_called_once()
        ig_pub.assert_called_once()
        post = SocialQueuePost.objects.get(source=SocialQueuePost.SOURCE_GAME)
        self.assertIn('уже завтра', post.caption)
        self.assertIn('по МСК', post.caption)
        self.assertIn('зарегистрировать', post.caption)
        self.assertTrue(post.image)
        self.assertIn('/games/life_game/', post.play_url)

    @patch('games.telegram.scheduling.publish_instagram')
    @patch('games.telegram.scheduling.publish_twitter')
    @patch('games.telegram.scheduling.publish_telegram')
    @patch('games.telegram.scheduling._game_announce_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_hour_before(self, _admin, announce_mock, _png, _tg, _tw, _ig):
        self._set_window(start_delta=timedelta(minutes=40), end_delta=timedelta(hours=3))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['day_before'], 1)
        self.assertEqual(stats['hour_before'], 1)
        kinds = set(
            TelegramGameAnnouncement.objects.filter(game=self.game).values_list('kind', flat=True)
        )
        self.assertIn(TelegramGameAnnouncement.KIND_DAY_BEFORE, kinds)
        self.assertIn(TelegramGameAnnouncement.KIND_HOUR_BEFORE, kinds)
        hour_texts = [c.args[0] for c in announce_mock.call_args_list]
        self.assertTrue(any('Через час' in t for t in hour_texts))
        self.assertFalse(any('уже завтра' in t for t in hour_texts))

    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_start_uses_start_time_not_visible(self, _admin, announce_mock):
        # Real start already passed; visible still in the future — must still announce start.
        self.game.start_time = self.now - timedelta(minutes=1)
        self.game.end_time = self.now + timedelta(hours=2)
        self.game.visible_start_time = self.now + timedelta(hours=1)
        self.game.visible_end_time = self.now + timedelta(hours=3)
        self.game.save()
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['start'], 1)
        text = announce_mock.call_args.args[0]
        self.assertIn('Начали!', text)

    @patch('games.telegram.scheduling.send_announce_photo')
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_end_soon_15_and_end_respect_extended_end(self, _admin, announce_mock, _photo):
        self._set_window(start_delta=timedelta(hours=-2), end_delta=timedelta(minutes=10))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['end_soon_15'], 1)
        self.assertEqual(stats['end'], 0)

        # Extend end past the 15-minute window — end must wait for new end_time.
        self.game.end_time = self.now + timedelta(hours=1)
        self.game.save(update_fields=['end_time'])
        announce_mock.reset_mock()
        stats2 = process_game_announcements(now=self.now)
        self.assertEqual(stats2['end_soon_15'], 0)  # already sent
        self.assertEqual(stats2['end'], 0)

        later = self.now + timedelta(hours=1, minutes=1)
        stats3 = process_game_announcements(now=later)
        self.assertEqual(stats3['end'], 1)
        end_texts = [c.args[0] for c in announce_mock.call_args_list]
        self.assertTrue(any('завершилась' in t for t in end_texts))
        self.assertTrue(any('answers' in t for t in end_texts))

    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_day_hour_not_after_start(self, _admin, announce_mock):
        self._set_window(start_delta=timedelta(minutes=-5), end_delta=timedelta(hours=2))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['day_before'], 0)
        self.assertEqual(stats['hour_before'], 0)
        self.assertEqual(stats['start'], 1)

    @patch('games.telegram.scheduling.publish_instagram')
    @patch('games.telegram.scheduling.publish_twitter')
    @patch('games.telegram.scheduling.publish_telegram')
    @patch('games.telegram.scheduling._game_announce_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_admin_start_soon_once_in_window(self, admin_mock, _announce, _png, _tg, _tw, _ig):
        # Admin window: visible_start −65 … −55 minutes (default visible = start).
        self._set_window(start_delta=timedelta(minutes=60), end_delta=timedelta(hours=3))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['admin_start_soon'], 1)
        self.assertEqual(admin_mock.call_count, 1)
        self.assertIn('Через час начинается игра', admin_mock.call_args.args[0])
        self.assertTrue(
            TelegramGameAnnouncement.objects.filter(
                game=self.game,
                kind=TelegramGameAnnouncement.KIND_ADMIN_START_SOON,
            ).exists()
        )

        # Same minute / later ticks in the window must not re-send (multi-instance safe).
        stats2 = process_game_announcements(now=self.now + timedelta(minutes=3))
        self.assertEqual(stats2['admin_start_soon'], 0)
        self.assertEqual(admin_mock.call_count, 1)

    @patch('games.telegram.scheduling._game_announce_png', return_value=None)
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_day_before_waits_for_screenshot(self, _admin, announce_mock, _png):
        self._set_window(start_delta=timedelta(hours=20), end_delta=timedelta(hours=23))
        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['day_before'], 0)
        announce_mock.assert_not_called()
        self.assertFalse(
            TelegramGameAnnouncement.objects.filter(
                game=self.game, kind=TelegramGameAnnouncement.KIND_DAY_BEFORE,
            ).exists()
        )
        self.assertFalse(SocialQueuePost.objects.filter(source=SocialQueuePost.SOURCE_GAME).exists())


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_ANNOUNCE_CHAT_IDS=['-100999'],
    SITE_BASE_URL='https://interoves.com',
    TELEGRAM_LADDER_SCREENSHOT=False,
)
class ChatAllSolvedAndResultsTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()
        self.game = Game.objects.create(
            id='solve_game',
            name='Solve Game',
            no_html_name='Solve Game',
            author='a',
            answers_url='https://docs.google.com/document/d/answers',
            tags={'telegram_announce': True},
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            start_time=self.now - timedelta(hours=1),
            end_time=self.now + timedelta(hours=1),
        )
        self.tg = TaskGroup.objects.create(label='solve tg')
        self.task1 = Task.objects.create(task_group=self.tg, number='1', text='t1', points=10)
        self.task2 = Task.objects.create(task_group=self.tg, number='2', text='t2', points=10)
        GameTaskGroup.objects.create(game=self.game, task_group=self.tg, number='1', name='S')
        self.team = Team.objects.create(name='winners', visible_name='Winners')

    def _ok_attempt(self, task, team, when):
        attempt = Attempt.manager.create(
            game=self.game, task=task, team=team,
            text='ok', status='Ok', points=10,
        )
        Attempt.manager.filter(pk=attempt.pk).update(time=when)
        return attempt

    @patch('games.telegram.scheduling._tournament_results_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_photo')
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_all_solved_once_per_team(self, _admin, _msg, photo_mock, _png):
        photo_mock.return_value = True
        when = self.now - timedelta(minutes=30)
        self._ok_attempt(self.task1, self.team, when)
        self._ok_attempt(self.task2, self.team, when)

        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['all_solved'], 1)
        photo_mock.assert_called()
        caption = photo_mock.call_args.kwargs.get('caption') or ''
        self.assertIn('Winners', caption)
        self.assertIn('все задания', caption)
        self.assertIn('взяла 0 подсказок', caption)
        self.assertIn('потеряла на этом 0 баллов', caption)
        self.assertNotIn('member', caption.lower())

        photo_mock.reset_mock()
        stats2 = process_game_announcements(now=self.now)
        self.assertEqual(stats2['all_solved'], 0)
        photo_mock.assert_not_called()

    @patch('games.telegram.scheduling._tournament_results_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_photo')
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_all_solved_includes_hint_stats(self, _admin, _msg, photo_mock, _png):
        photo_mock.return_value = True
        when = self.now - timedelta(minutes=30)
        self._ok_attempt(self.task1, self.team, when)
        self._ok_attempt(self.task2, self.team, when)
        h1 = Hint.objects.create(task=self.task1, number='1', text='h1', points_penalty=1)
        h2 = Hint.objects.create(task=self.task2, number='1', text='h2', points_penalty=2)
        HintAttempt.objects.create(team=self.team, hint=h1, is_real_request=True)
        HintAttempt.objects.create(team=self.team, hint=h2, is_real_request=True)
        # Watching / not a real take — must not count.
        HintAttempt.objects.create(team=self.team, hint=h1, is_real_request=False)

        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['all_solved'], 1)
        caption = photo_mock.call_args.kwargs.get('caption') or ''
        self.assertIn('взяла 2 подсказки', caption)
        self.assertIn('потеряла на этом 3 балла', caption)

    @patch('games.telegram.scheduling.publish_instagram')
    @patch('games.telegram.scheduling.publish_twitter')
    @patch('games.telegram.scheduling.publish_telegram')
    @patch('games.telegram.scheduling._tournament_results_png', return_value=_FAKE_PNG)
    @patch('games.telegram.scheduling.send_announce_photo')
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_results_waits_for_pending_then_sends(
        self, _admin, msg_mock, photo_mock, _png, tg_pub, tw_pub, ig_pub,
    ):
        self.game.start_time = self.now - timedelta(hours=3)
        self.game.end_time = self.now - timedelta(minutes=5)
        self.game.save(update_fields=['start_time', 'end_time'])

        # Mark start/end already sent so we only care about results.
        TelegramGameAnnouncement.objects.create(
            game=self.game, kind=TelegramGameAnnouncement.KIND_START,
        )
        TelegramGameAnnouncement.objects.create(
            game=self.game, kind=TelegramGameAnnouncement.KIND_END,
        )

        when = self.now - timedelta(hours=2)
        self._ok_attempt(self.task1, self.team, when)
        pending = Attempt.manager.create(
            game=self.game, task=self.task2, team=self.team,
            text='p', status='Pending', points=0,
        )
        Attempt.manager.filter(pk=pending.pk).update(time=when)

        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['results'], 0)
        photo_mock.assert_not_called()
        tg_pub.assert_not_called()
        self.assertFalse(SocialQueuePost.objects.filter(source=SocialQueuePost.SOURCE_GAME).exists())

        Attempt.manager.filter(pk=pending.pk).update(status='Ok', points=10)
        stats2 = process_game_announcements(now=self.now)
        self.assertEqual(stats2['results'], 1)
        photo_mock.assert_not_called()
        msg_mock.assert_not_called()
        tg_pub.assert_called_once()
        tw_pub.assert_called_once()
        ig_pub.assert_called_once()
        post = SocialQueuePost.objects.get(source=SocialQueuePost.SOURCE_GAME)
        self.assertIn('Результаты', post.caption)
        self.assertIn('Winners', post.caption)
        self.assertIn('tournament-results', post.caption)
        self.assertTrue(post.image)

    @patch('games.telegram.scheduling._tournament_results_png', return_value=None)
    @patch('games.telegram.scheduling.send_announce_photo')
    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_results_waits_for_screenshot(self, _admin, msg_mock, photo_mock, _png):
        self.game.start_time = self.now - timedelta(hours=3)
        self.game.end_time = self.now - timedelta(minutes=5)
        self.game.save(update_fields=['start_time', 'end_time'])
        TelegramGameAnnouncement.objects.create(
            game=self.game, kind=TelegramGameAnnouncement.KIND_START,
        )
        TelegramGameAnnouncement.objects.create(
            game=self.game, kind=TelegramGameAnnouncement.KIND_END,
        )
        when = self.now - timedelta(hours=2)
        self._ok_attempt(self.task1, self.team, when)
        self._ok_attempt(self.task2, self.team, when)

        stats = process_game_announcements(now=self.now)
        self.assertEqual(stats['results'], 0)
        photo_mock.assert_not_called()
        msg_mock.assert_not_called()
        self.assertFalse(
            TelegramGameAnnouncement.objects.filter(
                game=self.game, kind=TelegramGameAnnouncement.KIND_RESULTS,
            ).exists()
        )

    def test_results_formatter_mass_first_place(self):
        teams = [
            Team.objects.create(name='t{}'.format(i), visible_name='Team {}'.format(i))
            for i in range(4)
        ]
        podium = {1: teams}
        text = format_game_results_announcement(self.game, podium)
        self.assertIn('1 место (4 команды)', text)
        self.assertNotIn('2 место', text)

    def test_results_formatter_podium(self):
        a = Team.objects.create(name='a', visible_name='Alpha')
        b = Team.objects.create(name='b', visible_name='Beta')
        c = Team.objects.create(name='c', visible_name='Gamma')
        text = format_game_results_announcement(self.game, {1: [a], 2: [b], 3: [c]})
        self.assertIn('1 место', text)
        self.assertIn('2 место', text)
        self.assertIn('3 место', text)
        self.assertIn('Alpha', text)

    def test_all_solved_formatter_uses_team_name_only(self):
        text = format_all_solved_announcement(self.game, self.team)
        self.assertIn('Winners', text)
        self.assertNotIn('@', text)
        self.assertIn('взяла 0 подсказок', text)

    def test_all_solved_desyatochka_uses_prepositional(self):
        self.game.no_html_name = 'Десяточка 170'
        self.game.save(update_fields=['no_html_name'])
        text = format_all_solved_announcement(self.game, self.team)
        self.assertIn('в Десяточке 170!', text)
        self.assertNotIn('в игре', text)

    def test_all_solved_other_name_uses_v_igre(self):
        text = format_all_solved_announcement(self.game, self.team)
        self.assertIn('в игре Solve Game!', text)

    def test_hints_taken_line_plurals(self):
        self.assertEqual(
            format_hints_taken_line(1, 1),
            'Команда взяла 1 подсказку и потеряла на этом 1 балл.',
        )
        self.assertEqual(
            format_hints_taken_line(2, 3),
            'Команда взяла 2 подсказки и потеряла на этом 3 балла.',
        )
        self.assertEqual(
            format_hints_taken_line(5, 11),
            'Команда взяла 5 подсказок и потеряла на этом 11 баллов.',
        )
        self.assertEqual(
            format_hints_taken_line(21, 1.5),
            'Команда взяла 21 подсказку и потеряла на этом 1.5 балла.',
        )

    def test_reminder_formatters(self):
        day = format_game_day_before_announcement(self.game)
        hour = format_game_hour_before_announcement(self.game)
        self.assertIn('уже завтра', day)
        self.assertIn('по МСК', day)
        self.assertIn('билеты', day)
        self.assertNotIn('билеты', hour)
        self.assertNotIn('зарегистрировать', hour)
        self.assertIn('Через час', hour)

    def test_build_podium(self):
        a = Team.objects.create(name='pa', visible_name='A')
        b = Team.objects.create(name='pb', visible_name='B')
        podium = build_podium({a: 1, b: 2})
        self.assertEqual(podium[1], [a])
        self.assertEqual(podium[2], [b])
