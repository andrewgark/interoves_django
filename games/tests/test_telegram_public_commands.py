from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from games.models import Attempt, Game, HTMLPage, Project, Task, TaskGroup, Team
from games.telegram.public_commands import (
    NO_GAME_REPLY,
    first_place_teams,
    format_des_card_caption,
    format_des_results_caption,
    format_des_status_line,
    format_duration,
    parse_public_command,
    select_desyatka_for_public,
)


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


def _make_game(game_id, *, start, end, **kwargs):
    defaults = {
        'name': game_id,
        'author': 'Автор',
        'theme': 'Тема',
        'is_ready': True,
        'is_playable': True,
        'is_tournament': True,
        'start_time': start,
        'end_time': end,
        'project_id': 'main',
    }
    defaults.update(kwargs)
    return Game.objects.create(id=game_id, **defaults)


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_NOTIFY_CHAT_ID='12345',
    TELEGRAM_ANNOUNCE_CHAT_IDS=['-100999'],
    SITE_BASE_URL='https://interoves.com',
    LANGUAGE_CODE='ru-ru',
)
class PublicDesSelectionTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()

    def test_parse_public_command(self):
        self.assertEqual(parse_public_command('/des'), '/des')
        self.assertEqual(parse_public_command('/des@MyBot'), '/des')
        self.assertEqual(parse_public_command('/des_results'), '/des_results')
        self.assertIsNone(parse_public_command('/status'))
        self.assertIsNone(parse_public_command('des'))

    def test_prefers_soonest_upcoming(self):
        _make_game('far', start=self.now + timedelta(days=10), end=self.now + timedelta(days=10, hours=3))
        near = _make_game('near', start=self.now + timedelta(hours=2), end=self.now + timedelta(hours=5))
        _make_game(
            'old',
            start=self.now - timedelta(days=3),
            end=self.now - timedelta(days=3, hours=-3),
        )
        self.assertEqual(select_desyatka_for_public(now=self.now).id, near.id)

    def test_live_when_no_upcoming(self):
        live = _make_game(
            'live',
            start=self.now - timedelta(hours=1),
            end=self.now + timedelta(hours=2),
        )
        _make_game(
            'ended_long_ago',
            start=self.now - timedelta(days=5),
            end=self.now - timedelta(days=5) + timedelta(hours=3),
        )
        self.assertEqual(select_desyatka_for_public(now=self.now).id, live.id)

    def test_recently_ended_within_24h(self):
        recent = _make_game(
            'recent',
            start=self.now - timedelta(hours=5),
            end=self.now - timedelta(hours=2),
        )
        self.assertEqual(select_desyatka_for_public(now=self.now).id, recent.id)

    def test_none_when_ended_over_24h_ago(self):
        _make_game(
            'old',
            start=self.now - timedelta(days=3),
            end=self.now - timedelta(days=2),
        )
        self.assertIsNone(select_desyatka_for_public(now=self.now))

    def test_skips_not_ready(self):
        _make_game(
            'hidden',
            start=self.now + timedelta(hours=1),
            end=self.now + timedelta(hours=4),
            is_ready=False,
        )
        self.assertIsNone(select_desyatka_for_public(now=self.now))

    def test_upcoming_beats_live(self):
        _make_game(
            'live',
            start=self.now - timedelta(hours=1),
            end=self.now + timedelta(hours=2),
        )
        future = _make_game(
            'future',
            start=self.now + timedelta(days=1),
            end=self.now + timedelta(days=1, hours=3),
        )
        self.assertEqual(select_desyatka_for_public(now=self.now).id, future.id)


@override_settings(LANGUAGE_CODE='ru-ru', TIME_ZONE='Europe/Moscow')
class PublicDesFormattingTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()
        self.game = _make_game(
            'fmt',
            start=self.now + timedelta(hours=2, minutes=15),
            end=self.now + timedelta(hours=5),
            name='Десяточка №999',
            outside_name='Десяточка №999',
            no_html_name='Десяточка №999',
            theme='Палиндромы',
            author='Тестер',
        )

    def test_format_duration(self):
        self.assertEqual(
            format_duration(timedelta(days=1, hours=2, minutes=5)),
            '1 день 2 часа 5 минут',
        )
        self.assertEqual(format_duration(timedelta(minutes=40)), '40 минут')
        self.assertEqual(
            format_duration(timedelta(days=5, hours=18, minutes=59)),
            '5 дней 18 часов 59 минут',
        )

    def test_status_upcoming(self):
        text = format_des_status_line(self.game, now=self.now)
        self.assertTrue(text.startswith('До начала: '))
        self.assertNotIn('через', text)

    def test_status_live(self):
        self.game.start_time = self.now - timedelta(hours=1)
        self.game.end_time = self.now + timedelta(minutes=45)
        self.game.save()
        text = format_des_status_line(self.game, now=self.now)
        self.assertIn('Идёт уже', text)
        self.assertIn('осталось', text)

    def test_status_ended(self):
        self.game.start_time = self.now - timedelta(hours=5)
        self.game.end_time = self.now - timedelta(hours=2)
        self.game.save()
        text = format_des_status_line(self.game, now=self.now)
        self.assertTrue(text.startswith('Закончилась '))
        self.assertTrue(text.endswith(' назад'))

    def test_card_caption_fields(self):
        caption = format_des_card_caption(self.game, now=self.now)
        self.assertIn('Десяточка №999', caption)
        self.assertIn('Тема: Палиндромы', caption)
        self.assertIn('Автор: Тестер', caption)
        self.assertIn('До начала:', caption)
        self.assertNotIn('через', caption)


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    SITE_BASE_URL='https://interoves.com',
    LANGUAGE_CODE='ru-ru',
)
class PublicDesResultsHelpersTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()
        self.game = _make_game(
            'res',
            start=self.now - timedelta(hours=3),
            end=self.now - timedelta(hours=1),
            name='Res Game',
            no_html_name='Res Game',
        )
        self.task_group = TaskGroup.objects.create(label='res tg')
        self.task = Task.objects.create(task_group=self.task_group, number='1', text='t', points=10)
        from games.models import GameTaskGroup
        GameTaskGroup.objects.create(game=self.game, task_group=self.task_group, number='1', name='S')
        self.team_a = Team.objects.create(name='a', visible_name='Alpha')
        self.team_b = Team.objects.create(name='b', visible_name='Beta')
        attempt_time = self.now - timedelta(hours=2)
        for team in (self.team_a, self.team_b):
            attempt = Attempt.manager.create(
                game=self.game, task=self.task, team=team,
                text='ok', status='Ok', points=10,
            )
            Attempt.manager.filter(pk=attempt.pk).update(time=attempt_time)


    def test_first_place_can_be_tie(self):
        winners = first_place_teams(self.game)
        names = sorted(t.visible_name for t in winners)
        self.assertEqual(names, ['Alpha', 'Beta'])

    def test_results_caption_lists_winners_and_link(self):
        winners = first_place_teams(self.game)
        caption = format_des_results_caption(self.game, winners)
        self.assertIn('1 место (2):', caption)
        self.assertIn('Alpha', caption)
        self.assertIn('Beta', caption)
        self.assertIn('/games/res/tournament-results/', caption)


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_WEBHOOK_SECRET='secret',
    SITE_BASE_URL='https://interoves.com',
    LANGUAGE_CODE='ru-ru',
)
class PublicDesWebhookTests(TestCase):
    def setUp(self):
        _ensure_reference_rows()
        self.now = timezone.now()

    @patch('games.telegram.public_commands.send_message')
    def test_des_no_game(self, send_message_mock):
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': 777, 'type': 'private'},
                    'text': '/des',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once_with(777, NO_GAME_REPLY)

    @patch('games.telegram.public_commands.send_photo')
    @patch('games.telegram.public_commands.send_message')
    def test_des_with_image(self, send_message_mock, send_photo_mock):
        send_photo_mock.return_value = {'message_id': 1}
        game = _make_game(
            'des_img',
            start=self.now + timedelta(hours=3),
            end=self.now + timedelta(hours=6),
            name='Soon',
            no_html_name='Soon',
            theme='Тема X',
            author='Автор Y',
        )
        game.image = SimpleUploadedFile('poster.jpg', b'\xff\xd8\xffjpeg', content_type='image/jpeg')
        game.save()

        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': -100999, 'type': 'supergroup', 'title': 'Десяточек'},
                    'text': '/des@bot',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_photo_mock.assert_called_once()
        caption = send_photo_mock.call_args.kwargs.get('caption') or ''
        self.assertIn('Soon', caption)
        self.assertIn('Тема: Тема X', caption)
        self.assertIn('Автор: Автор Y', caption)
        send_message_mock.assert_not_called()

    @patch('games.telegram.webhook.send_message')
    def test_admin_command_still_rejected_in_group(self, send_message_mock):
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': -100999, 'type': 'supergroup'},
                    'text': '/status',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        self.assertIn('admin-чате', send_message_mock.call_args.args[1])

    @patch('games.telegram.results_image.render_tournament_results_png')
    @patch('games.telegram.public_commands.send_photo')
    @patch('games.telegram.public_commands.send_message')
    def test_des_results_sends_photo(self, send_message_mock, send_photo_mock, render_mock):
        send_photo_mock.return_value = {'message_id': 2}
        render_mock.return_value = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
        _make_game(
            'des_live',
            start=self.now - timedelta(hours=1),
            end=self.now + timedelta(hours=2),
            name='Live',
            no_html_name='Live',
        )
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': 42, 'type': 'private'},
                    'text': '/des_results',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        send_photo_mock.assert_called_once()
        caption = send_photo_mock.call_args.kwargs.get('caption') or ''
        self.assertIn('Live', caption)
        self.assertIn('tournament-results', caption)
        send_message_mock.assert_not_called()

    @patch('games.telegram.public_commands.send_message')
    def test_des_results_before_start(self, send_message_mock):
        _make_game(
            'des_soon',
            start=self.now + timedelta(hours=4),
            end=self.now + timedelta(hours=7),
        )
        response = self.client.post(
            '/telegram/webhook/secret/',
            data={
                'message': {
                    'chat': {'id': 42, 'type': 'private'},
                    'text': '/des_results',
                },
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        text = send_message_mock.call_args.args[1]
        self.assertIn('ещё недоступны', text)
