from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from games.models import Attempt, BugReport, Game, HTMLPage, Project, Registration, Task, TaskGroup, Team, TicketRequest
from games.telegram.admin_commands import handle_admin_command, registration_milestone_reached
from games.telegram.digest import build_daily_digest, collect_daily_digest_stats
from games.telegram.announcements import (
    format_game_end_announcement,
    format_game_end_soon_15_announcement,
    format_game_end_soon_announcement,
    format_game_start_announcement,
)
from games.telegram.config import is_admin_chat
from games.telegram.models import TelegramGameAnnouncement
from games.telegram.notify import (
    format_bug_report_message,
    format_payment_message,
    notify_new_bug_report,
    send_admin_message,
)


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
    TELEGRAM_NOTIFY_CHAT_ID='12345',
    TELEGRAM_ANNOUNCE_CHAT_IDS=['-100999'],
    SITE_BASE_URL='https://interoves.com',
)
class TelegramNotifyTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.game = Game.objects.create(
            id='tg_test_game',
            name='Test Game',
            author='author',
            game_url='https://docs.google.com/document/d/conditions',
            answers_url='https://docs.google.com/document/d/answers',
            standings_url='https://docs.google.com/spreadsheets/d/results',
            tags={'telegram_announce': True},
        )
        self.task_group = TaskGroup.objects.create(label='tg tg')
        self.task = Task.objects.create(task_group=self.task_group, number='1', text='task')
        self.team = Team.objects.create(name='tg_team', visible_name='TG Team')

    @patch('games.telegram.notify.send_message')
    def test_notify_bug_report_with_keyboard(self, send_message_mock):
        send_message_mock.return_value = True
        from games.models import GameTaskGroup
        GameTaskGroup.objects.create(
            game=self.game, task_group=self.task_group, number='1', name='Section',
        )
        report = BugReport.objects.create(
            game=self.game,
            task=self.task,
            team=self.team,
            text='Something broke',
        )
        notify_new_bug_report(report)
        send_message_mock.assert_called_once()
        kwargs = send_message_mock.call_args.kwargs
        self.assertIn('inline_keyboard', kwargs['reply_markup'])
        text = send_message_mock.call_args.args[1]
        self.assertIn('Something broke', text)
        self.assertIn('/games/{}/1/#new-task-{}'.format(self.game.id, self.task.pk), text)
        self.assertIn('/admin/games/task/{}/change/'.format(self.task.pk), text)

    def test_format_bug_report_message_links(self):
        from games.models import GameTaskGroup
        GameTaskGroup.objects.create(
            game=self.game, task_group=self.task_group, number='2', name='Round 2',
        )
        report = BugReport.objects.create(
            game=self.game,
            task=self.task,
            team=self.team,
            text='Broken image',
        )
        text = format_bug_report_message(report)
        self.assertIn('href="https://interoves.com/games/{}/2/#new-task-{}"'.format(
            self.game.id, self.task.pk,
        ), text)
        self.assertIn('href="https://interoves.com/admin/games/task/{}/change/"'.format(
            self.task.pk,
        ), text)
        self.assertIn('href="https://interoves.com/admin/games/taskgroup/{}/change/"'.format(
            self.task_group.pk,
        ), text)
        self.assertIn('href="https://interoves.com/admin/games/game/{}/change/"'.format(
            self.game.id,
        ), text)

    @patch('games.telegram.notify.send_message')
    def test_send_admin_message_respects_mute(self, send_message_mock):
        from games.telegram.config import set_admin_mute

        set_admin_mute(30)
        self.assertFalse(send_admin_message('muted'))
        send_message_mock.assert_not_called()

    def test_format_payment_message(self):
        ticket = TicketRequest.objects.create(team=self.team, tickets=2, money=4000, status='Accepted')
        text = format_payment_message(ticket, 'payment.succeeded')
        self.assertIn('Оплата', text)
        self.assertIn('TG Team', text)

    def test_announcement_messages_include_links(self):
        start = format_game_start_announcement(self.game)
        self.assertIn('Начали!', start)
        self.assertIn('conditions', start)
        self.assertIn('Сайт', start)
        end_soon = format_game_end_soon_announcement(self.game)
        self.assertIn('30 минут', end_soon)
        end_soon_15 = format_game_end_soon_15_announcement(self.game)
        self.assertIn('15 минут', end_soon_15)
        end = format_game_end_announcement(self.game)
        self.assertIn('завершилась', end)
        self.assertIn('answers', end)
        self.assertNotIn('Таблица результатов', end)

    def test_registration_milestone(self):
        self.assertEqual(registration_milestone_reached(9, 10), 10)
        self.assertIsNone(registration_milestone_reached(10, 11))

    def test_is_admin_chat(self):
        self.assertTrue(is_admin_chat('12345'))
        self.assertFalse(is_admin_chat('-100999'))

    @patch('games.telegram.scheduling.send_announce_message')
    @patch('games.telegram.scheduling.send_admin_message')
    def test_process_game_announcements_start(self, _admin_mock, announce_mock):
        from games.telegram.scheduling import process_game_announcements

        now = timezone.now()
        self.game.start_time = now - timezone.timedelta(minutes=1)
        self.game.end_time = now + timezone.timedelta(hours=2)
        self.game.visible_start_time = self.game.start_time
        self.game.visible_end_time = self.game.end_time
        self.game.save()

        stats = process_game_announcements(now=now)
        self.assertEqual(stats['start'], 1)
        announce_mock.assert_called()
        self.assertTrue(
            TelegramGameAnnouncement.objects.filter(
                game=self.game,
                kind=TelegramGameAnnouncement.KIND_START,
            ).exists()
        )


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_WEBHOOK_SECRET='secret',
)
class TelegramWebhookTests(TestCase):
    def test_webhook_rejects_bad_secret(self):
        response = self.client.post('/telegram/webhook/wrong/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    @patch('games.telegram.webhook.send_message')
    def test_admin_help_command(self, send_message_mock):
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': 12345, 'type': 'private'},
                    'text': '/help',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        self.assertIn('Admin mode', send_message_mock.call_args.args[1])

    @patch('games.telegram.webhook.send_message')
    def test_group_chat_rejects_commands(self, send_message_mock):
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': -100999, 'type': 'supergroup', 'title': 'Десяточек'},
                    'text': '/status',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        self.assertIn('admin-чате', send_message_mock.call_args.args[1])


class TelegramCommandTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.game = Game.objects.create(id='cmd_game', name='Cmd Game', author='a')

    def test_game_command(self):
        text = handle_admin_command('/game cmd_game')
        self.assertIn('Cmd Game', text)
        self.assertIn('cmd_game', text)

    def test_unknown_command(self):
        text = handle_admin_command('/nope')
        self.assertIn('Неизвестная', text)


class TelegramDigestTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.game_a = Game.objects.create(id='digest_a', name='Digest A', author='a')
        self.game_b = Game.objects.create(id='digest_b', name='Digest B', author='a')
        self.task_group = TaskGroup.objects.create(label='digest tg')
        self.task = Task.objects.create(task_group=self.task_group, number='1', text='task')
        self.team = Team.objects.create(name='digest_team', visible_name='Digest Team')
        self.user = User.objects.create_user(username='digest_user', password='x')
        self.user2 = User.objects.create_user(username='digest_user2', password='x')

    def test_digest_counts_attempts_and_top_games(self):
        now = timezone.now()
        Attempt.manager.create(
            game=self.game_a, task=self.task, team=self.team, user=self.user,
            text='a', status='Ok', time=now,
        )
        Attempt.manager.create(
            game=self.game_a, task=self.task, team=self.team, user=self.user2,
            text='b', status='Wrong', time=now,
        )
        Attempt.manager.create(
            game=self.game_b, task=self.task, team=self.team, user=self.user,
            text='c', status='Ok', time=now,
        )

        stats = collect_daily_digest_stats(since=now - timezone.timedelta(hours=1))
        self.assertEqual(stats['attempts_total'], 3)
        self.assertEqual(stats['active_users'], 2)
        self.assertEqual(stats['top_games_attempts'][0]['game_id'], 'digest_a')
        self.assertEqual(stats['top_games_attempts'][0]['attempts'], 2)

        text = build_daily_digest(since=now - timezone.timedelta(hours=1))
        self.assertIn('Посылки', text)
        self.assertIn('Digest A', text)
        self.assertIn('2 пользов.', text)

    def test_digest_command(self):
        text = handle_admin_command('/digest')
        self.assertIn('Дайджест', text)
        self.assertIn('Посылки', text)
