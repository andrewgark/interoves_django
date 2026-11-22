from datetime import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from games.models import (
    AlphabettyDictSuggestion,
    Attempt,
    BugReport,
    CensorlyReviewCase,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.ops.telegram.bot.callbacks import handle_callback_query
from games.ops.telegram.bot.commands import handle_admin_command
from games.ops.telegram.bot.notify import (
    format_alphabetty_dict_suggestion_message,
    format_bug_report_message,
    format_payment_message,
    notify_admin_site_error,
    notify_admin_word_salad_submission_error,
    notify_admin_zero_duration_completion,
    notify_new_bug_report,
    send_admin_message,
    send_announce_message,
    send_announce_photo,
)
from games.ops.telegram.channels.announcements import (
    format_game_day_before_announcement,
    format_game_end_announcement,
    format_game_end_soon_15_announcement,
    format_game_end_soon_announcement,
    format_game_start_announcement,
)
from games.ops.telegram.channels.digest import build_daily_digest, collect_daily_digest_stats
from games.ops.telegram.models import TelegramGameAnnouncement
from games.ops.telegram.transport.api import _safe_error_text
from games.ops.telegram.transport.config import is_admin_chat


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


@override_settings(TELEGRAM_BOT_TOKEN='real-looking-test-token')
class TelegramApiLoggingTests(SimpleTestCase):
    def test_provider_token_is_redacted_from_error_text(self):
        error = RuntimeError(
            'request failed for https://api.telegram.org/botreal-looking-test-token/sendMessage'
        )
        self.assertNotIn('real-looking-test-token', _safe_error_text(error))
        self.assertIn('[redacted]', _safe_error_text(error))


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

    @override_settings(TELEGRAM_ANNOUNCE_CHAT_IDS=['chat-1', 'chat-2'])
    @patch('games.ops.telegram.bot.notify.send_message', side_effect=[True, False])
    def test_announce_message_requires_success_in_every_chat(self, send_mock):
        self.assertFalse(send_announce_message('hello'))
        self.assertEqual(send_mock.call_count, 2)

    @override_settings(TELEGRAM_ANNOUNCE_CHAT_IDS=['chat-1', 'chat-2'])
    @patch('games.ops.telegram.bot.notify.send_photo', side_effect=[{'message_id': 1}, None])
    def test_announce_photo_requires_success_in_every_chat(self, send_mock):
        self.assertFalse(send_announce_photo(b'png'))
        self.assertEqual(send_mock.call_count, 2)

    @patch('games.ops.telegram.bot.notify.send_message_result')
    def test_notify_bug_report_with_keyboard(self, send_message_mock):
        send_message_mock.return_value = {'message_id': 1}
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
        keyboard = str(kwargs['reply_markup'])
        self.assertIn('bug:reviewed:{}'.format(report.pk), keyboard)
        self.assertIn('bug:fixed:{}'.format(report.pk), keyboard)
        self.assertIn('bug:dismiss:{}'.format(report.pk), keyboard)
        text = send_message_mock.call_args.args[1]
        self.assertIn('Something broke', text)
        self.assertIn('/games/{}/1/#new-task-{}'.format(self.game.id, self.task.pk), text)
        self.assertIn('/support/preview/games/{}/1/'.format(self.game.id), text)
        self.assertTrue(text.startswith('🐞 <b>Баг #{}</b>'.format(report.pk)))
        self.assertNotIn('/admin/', text)

    @patch('games.ops.telegram.bot.notify.send_message_result')
    def test_notify_censorly_word_report_with_review_keyboard(self, send_message_mock):
        send_message_mock.return_value = {'message_id': 1}
        report = BugReport.objects.create(
            report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH,
            game=self.game,
            task=self.task,
            team=self.team,
            text='Censorly report',
            metadata={
                'reason': 'FALSE_POSITIVE',
                'expected': 'CLOSED',
                'article': 'Китай',
                'target_surface': 'поддержка',
                'other_word': 'держать',
                'comment': 'Слишком слабая связь',
                'diagnostic': {
                    'runtime': 'OPEN',
                    'classification': 'D',
                    'recommendation': 'SEMANTIC_SPLIT',
                    'guess_structures': [['fam:держ']],
                    'target_structures': [['fam:держ']],
                },
            },
        )
        notify_new_bug_report(report)
        text = send_message_mock.call_args.args[1]
        keyboard = str(send_message_mock.call_args.kwargs['reply_markup'])
        self.assertIn('Жалоба на Цензурку #{}</b>: лишнее открытие'.format(report.pk), text)
        self.assertIn('Что поправить: <code>SEMANTIC_SPLIT</code>', text)
        self.assertIn('поддержка', text)
        self.assertIn('держать', text)
        self.assertIn('censorly:confirm:{}'.format(report.pk), keyboard)
        self.assertIn('censorly:reject:{}'.format(report.pk), keyboard)
        self.assertIn('censorly:review:{}'.format(report.pk), keyboard)

    @patch('games.ops.telegram.bot.notify.send_message')
    @patch('games.ops.telegram.bot.callbacks.answer_callback_query')
    @patch('games.ops.telegram.bot.callbacks.edit_message_reply_markup')
    def test_bug_fixed_callback(self, edit_markup_mock, answer_mock, send_message_mock):
        send_message_mock.return_value = True
        report = BugReport.objects.create(
            game=self.game,
            task=self.task,
            team=self.team,
            text='Need a fix',
        )
        send_message_mock.reset_mock()
        handle_callback_query({
            'id': 'cb-bug-fixed',
            'data': 'bug:fixed:{}'.format(report.pk),
            'message': {'chat': {'id': 12345}, 'message_id': 11},
        })
        report.refresh_from_db()
        self.assertEqual(report.status, BugReport.STATUS_FIXED)
        answer_mock.assert_called()
        edit_markup_mock.assert_called_once()
        keyboard = edit_markup_mock.call_args.kwargs['reply_markup']
        self.assertEqual(keyboard['inline_keyboard'][0][0]['text'], '✅ Исправлено')
        # The decision shows on the message itself; no extra chat message.
        send_message_mock.assert_not_called()

    @patch('games.puzzles.censorly.offline.tools.review_pair.record_review_case')
    @patch('games.ops.telegram.bot.callbacks.answer_callback_query')
    @patch('games.ops.telegram.bot.callbacks.edit_message_reply_markup')
    def test_censorly_confirm_creates_diagnosed_review_case(
        self, edit_markup_mock, answer_mock, record_mock,
    ):
        report = BugReport.objects.create(
            report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH,
            game=self.game,
            task=self.task,
            team=self.team,
            text='Censorly report',
            metadata={
                'reason': 'FALSE_NEGATIVE',
                'expected': 'OPEN',
                'article': 'Китай',
                'target_surface': 'сенатор',
                'other_word': 'сенат',
                'comment': 'Очевидная семья',
            },
        )
        review_case = CensorlyReviewCase.objects.create(
            pair_key='сенат\x1fсенатор',
            guess='сенат',
            target='сенатор',
            expected='OPEN',
            actual='CLOSED',
        )
        record_mock.return_value = (
            {'status': 'DIAGNOSED'}, True, {'runtime_reason': 'CLOSED'}, review_case,
        )
        handle_callback_query({
            'id': 'cb-censorly-confirm',
            'data': 'censorly:confirm:{}'.format(report.pk),
            'message': {'chat': {'id': 12345}, 'message_id': 12},
        })
        report.refresh_from_db()
        self.assertEqual(report.status, BugReport.STATUS_REVIEWED)
        self.assertTrue(report.metadata['review_case_created'])
        self.assertEqual(report.metadata['review_queue_status'], 'DIAGNOSED')
        self.assertEqual(report.censorly_review_case_id, review_case.pk)
        record_mock.assert_called_once()
        self.assertEqual(record_mock.call_args.args[:3], ('сенат', 'сенатор', 'OPEN'))
        answer_mock.assert_called()
        edit_markup_mock.assert_called_once()

    @patch('games.ops.telegram.bot.notify.send_message')
    def test_dict_suggestion_notify_on_create(self, send_message_mock):
        send_message_mock.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            suggestion = AlphabettyDictSuggestion.objects.create(
                word='ТЕСТСЛОВОДЛЯБОТА',
                anon_key='anon123456',
            )
        self.assertEqual(send_message_mock.call_count, 1)
        text = send_message_mock.call_args.args[1]
        self.assertIn('ТЕСТСЛОВОДЛЯБОТА', text)
        self.assertIn('Алфавитки', text)
        keyboard = send_message_mock.call_args.kwargs['reply_markup']
        self.assertIn('abdict:approve:{}'.format(suggestion.pk), str(keyboard))
        self.assertIn('abdict:answer:{}'.format(suggestion.pk), str(keyboard))
        self.assertIn('abdict:reject:{}'.format(suggestion.pk), str(keyboard))

        # Повторный голос не спамит
        with self.captureOnCommitCallbacks(execute=True):
            suggestion.suggest_count += 1
            suggestion.save(update_fields=['suggest_count', 'updated_at'])
        self.assertEqual(send_message_mock.call_count, 1)

        msg = format_alphabetty_dict_suggestion_message(suggestion)
        self.assertIn('Голосов: 2', msg)
        self.assertNotIn('/admin/', msg)

    @patch('games.ops.telegram.bot.notify.send_message')
    def test_alphabetty_dict_suggestion_notify_on_create(self, send_message_mock):
        send_message_mock.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            suggestion = AlphabettyDictSuggestion.objects.create(
                word='РЕДКОЕСЛОВОДЛЯАЛФАВИТКИ',
                anon_key='anon123456',
            )
        self.assertEqual(send_message_mock.call_count, 1)
        text = send_message_mock.call_args.args[1]
        self.assertIn('Слово для словаря Алфавитки', text)
        self.assertIn('РЕДКОЕСЛОВОДЛЯАЛФАВИТКИ', text)
        keyboard = send_message_mock.call_args.kwargs['reply_markup']
        self.assertIn('abdict:approve:{}'.format(suggestion.pk), str(keyboard))
        self.assertIn('abdict:answer:{}'.format(suggestion.pk), str(keyboard))
        self.assertIn('abdict:reject:{}'.format(suggestion.pk), str(keyboard))

    @patch('games.ops.telegram.bot.notify.send_message')
    @patch('games.ops.telegram.bot.callbacks.answer_callback_query')
    @patch('games.ops.telegram.bot.callbacks.edit_message_reply_markup')
    def test_dict_suggestion_approve_callback(
        self, edit_markup_mock, answer_mock, send_message_mock,
    ):
        send_message_mock.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            suggestion = AlphabettyDictSuggestion.objects.create(
                word='ОДОБРИМЕНЯ',
            )
        send_message_mock.reset_mock()
        handle_callback_query({
            'id': 'cb1',
            'data': 'abdict:approve:{}'.format(suggestion.pk),
            'message': {'chat': {'id': 12345}, 'message_id': 9},
        })
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, AlphabettyDictSuggestion.STATUS_APPROVED)
        answer_mock.assert_called()
        edit_markup_mock.assert_called_once()

    @patch('games.ops.telegram.bot.notify.send_message')
    @patch('games.ops.telegram.bot.callbacks.answer_callback_query')
    @patch('games.ops.telegram.bot.callbacks.edit_message_reply_markup')
    def test_dict_suggestion_answer_callback(
        self, edit_markup_mock, answer_mock, send_message_mock,
    ):
        send_message_mock.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            suggestion = AlphabettyDictSuggestion.objects.create(word='ДЛЯЗАГАДКИ')
        send_message_mock.reset_mock()
        handle_callback_query({
            'id': 'cb2',
            'data': 'abdict:answer:{}'.format(suggestion.pk),
            'message': {'chat': {'id': 12345}, 'message_id': 10},
        })
        suggestion.refresh_from_db()
        self.assertEqual(
            suggestion.status,
            AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
        )
        answer_mock.assert_called()
        edit_markup_mock.assert_called_once()

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
        self.assertIn('href="https://interoves.com/support/preview/games/{}/2/"'.format(
            self.game.id,
        ), text)
        self.assertIn('href="https://interoves.com/support/game/{}/"'.format(self.game.id), text)
        self.assertIn('href="https://interoves.com/support/actor/team/{}/"'.format(self.team.name), text)
        self.assertIn('href="https://interoves.com/support/reports/{}/"'.format(report.pk), text)
        self.assertNotIn('/admin/', text)

    def test_format_bug_report_message_sections_game_uses_root_games_url(self):
        from games.models import GameTaskGroup
        from games.ops.telegram.links import game_play_path, task_play_url

        Project.objects.get_or_create(pk='sections', defaults={})
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        section_game = Game.objects.create(
            id='tg_replacements',
            name='Замены',
            author='a',
            author_extra='',
            project_id='sections',
        )
        scoped_game = Game.objects.create(
            id='tg_corp',
            name='Corp',
            author='a',
            author_extra='',
            project_id='glowbyte',
        )
        tg = TaskGroup.objects.create(label='sec tg')
        task = Task.objects.create(task_group=tg, number='1', text='t')
        GameTaskGroup.objects.create(game=section_game, task_group=tg, number='91', name='R91')

        self.assertEqual(game_play_path(section_game), '/games/tg_replacements/')
        self.assertEqual(
            task_play_url(section_game, task),
            'https://interoves.com/games/tg_replacements/91/#new-task-{}'.format(task.pk),
        )
        self.assertEqual(game_play_path(scoped_game), '/glowbyte/games/tg_corp/')

        report = BugReport.objects.create(
            game=section_game,
            task=task,
            team=self.team,
            text='Broken replacements link',
        )
        text = format_bug_report_message(report)
        self.assertIn(
            'href="https://interoves.com/games/tg_replacements/91/#new-task-{}"'.format(task.pk),
            text,
        )
        self.assertNotIn('/sections/', text)

    @patch('games.ops.telegram.bot.notify.send_message', return_value=True)
    def test_censorly_offer_is_announced_with_the_article_under_a_spoiler(self, send_message_mock):
        from games.club.offers.censorly import create_offer
        from games.models import Profile, RandomCensorlyGame

        user = User.objects.create(username='censorly_author')
        Profile.objects.create(user=user, first_name='Цен', last_name='Зор', telegram_username='cenzor')
        group = TaskGroup.objects.create(label='censorly random')
        Task.objects.create(task_group=group, number='1', task_type='censorly', text='')
        party = RandomCensorlyGame.objects.create(wiki_title='Москва', share_hash='cenhash1', task_group=group)
        puzzle = {'wiki_title': 'Москва', 'body_text': 'Москва — столица.', 'title_tokens': []}
        with patch('games.club.offers.censorly.profile_ready_for_offers', return_value=(True, [])), \
                patch('games.puzzles.censorly.play.puzzle_from_task', return_value=puzzle), \
                self.captureOnCommitCallbacks(execute=True):
            create_offer(user, party, comment='Хорошая статья')
            create_offer(user, party)  # the same article again: no second alert
        send_message_mock.assert_called_once()
        text = send_message_mock.call_args.args[1]
        self.assertIn('Новая цензурка на проверку', text)
        self.assertIn('(@cenzor)', text)
        self.assertIn('Статья: <tg-spoiler>Москва</tg-spoiler>', text)
        self.assertIn('Хорошая статья', text)
        self.assertIn('/support/censorly/', text)

    @patch('games.ops.telegram.bot.notify.send_message_result')
    def test_user_reply_is_threaded_under_the_report_message(self, send_mock):
        from games.models import BugReportMessage, Profile

        user = User.objects.create(username='thread_author')
        Profile.objects.create(user=user, first_name='Тред', last_name='Автор')
        send_mock.return_value = {'message_id': 4242}
        with self.captureOnCommitCallbacks(execute=True):
            report = BugReport.objects.create(game=self.game, task=self.task, user=user, text='Первое')
        report.refresh_from_db()
        self.assertEqual(report.metadata['telegram_message_id'], 4242)

        send_mock.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            BugReportMessage.objects.create(
                report=report, author_user=user, author_role=BugReportMessage.ROLE_USER, text='Ещё деталь',
            )
        self.assertEqual(send_mock.call_args.kwargs['reply_to_message_id'], 4242)
        self.assertIn('Ещё деталь', send_mock.call_args.args[1])

    def test_weekly_stale_bugs_lists_only_reports_waiting_for_an_answer(self):
        from datetime import timedelta

        from games.models import BugReportMessage, Profile
        from games.ops.telegram.bot.reports import build_stale_bugs_message

        now = timezone.now()
        user = User.objects.create(username='stale_author')
        Profile.objects.create(user=user, first_name='Ждёт', last_name='Ответа')

        def report(text, days, **fields):
            item = BugReport.objects.create(game=self.game, task=self.task, user=user, text=text, **fields)
            BugReport.objects.filter(pk=item.pk).update(time=now - timedelta(days=days))
            return item

        waiting = report('Висит давно', 10)
        answered = report('Уже ответили', 10)
        BugReportMessage.objects.create(
            report=answered, author_role=BugReportMessage.ROLE_ADMIN, text='Смотрим',
        )
        report('Свежий', 2)
        report('Исправлен', 10, status=BugReport.STATUS_FIXED)
        report('Жалоба на матчинг', 10, report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH)
        report('Совсем старый', 200)

        text = build_stale_bugs_message(now=now)
        self.assertIn('Баги без ответа больше 7 дней: 2', text)
        self.assertIn('/support/reports/{}/">#{}</a> · 10 дн.'.format(waiting.pk, waiting.pk), text)
        for absent in ('Уже ответили', 'Свежий', 'Исправлен', 'Жалоба на матчинг', 'Совсем старый'):
            self.assertNotIn(absent, text)
        self.assertIn('Ещё 1 старше 60 дней', text)

    @patch('games.ops.telegram.transport.api.requests.post')
    def test_rejected_html_is_resent_as_plain_text(self, post_mock):
        from requests import HTTPError

        from games.ops.telegram.transport.api import send_message

        bad = type('Resp', (), {})()
        bad.raise_for_status = lambda: (_ for _ in ()).throw(HTTPError('400 Client Error'))
        bad.json = lambda: {'ok': False, 'description': "Bad Request: can't parse entities: unclosed tag"}
        good = type('Resp', (), {})()
        good.raise_for_status = lambda: None
        good.json = lambda: {'ok': True, 'result': {}}
        post_mock.side_effect = [bad, good]

        with self.assertLogs('application', level='WARNING') as logs:
            self.assertTrue(send_message('12345', '<b>Баг #1</b> &lt;тег&gt; <i>оборван'))
        self.assertIn("can't parse entities", '\n'.join(logs.output))
        fallback = post_mock.call_args_list[1].kwargs['json']
        self.assertEqual(fallback['text'], 'Баг #1 <тег> оборван')
        self.assertNotIn('parse_mode', fallback)

    def test_bug_report_links_the_page_it_came_from(self):
        report = BugReport.objects.create(
            game=self.game, task=self.task, team=self.team, text='x',
            page_url='/games/tg_test_game/1/?x=1',
        )
        text = format_bug_report_message(report)
        self.assertIn(
            'Страница: <a href="https://interoves.com/games/tg_test_game/1/?x=1">interoves.com/games/tg_test_game/1/?x=1</a>',
            text,
        )

    def test_pending_command_links_bugs_to_support(self):
        report = BugReport.objects.create(game=self.game, task=self.task, team=self.team, text='Сломалась картинка')
        text = handle_admin_command('/pending')
        self.assertIn('<b>Ждут разбора</b>', text)
        self.assertIn('/support/reports/{}/">#{}</a>'.format(report.pk, report.pk), text)
        self.assertIn('Сломалась картинка', text)

    @patch('games.ops.telegram.bot.notify.send_message')
    def test_send_admin_message_respects_mute(self, send_message_mock):
        from games.ops.telegram.transport.config import clear_admin_mute, set_admin_mute

        set_admin_mute(30)
        # The mute lives in the shared cache; leaving it set silences later test modules.
        self.addCleanup(clear_admin_mute)
        self.assertFalse(send_admin_message('muted'))
        send_message_mock.assert_not_called()

    @patch('games.ops.telegram.bot.notify.publish_admin_alert', return_value=True)
    def test_zero_duration_completion_is_queued_with_dedupe_key(self, publish_mock):
        self.assertTrue(notify_admin_zero_duration_completion(
            game=self.game,
            task_group=self.task_group,
            actor={'team': self.team, 'user': None, 'anon_key': None},
            timing=None,
            completion={'record': None},
        ))
        publish_mock.assert_called_once()
        kwargs = publish_mock.call_args.kwargs
        self.assertEqual(kwargs['alert'], 'zero_duration_completion')
        self.assertIn('tg_test_game:{}'.format(self.task_group.pk), kwargs['dedupe_key'])
        self.assertIn('временем 0:00', kwargs['payload']['text'])

    @patch('games.ops.telegram.bot.notify.publish_admin_alert', return_value=True)
    @patch('games.ops.telegram.bot.notify.timezone.localtime')
    @patch('games.ops.telegram.bot.notify.timezone.now')
    def test_site_error_notification_includes_local_time(
        self, now_mock, localtime_mock, publish_mock,
    ):
        now_mock.return_value = datetime(2026, 9, 23, 9, 34, 56)
        localtime_mock.return_value = datetime(2026, 9, 23, 12, 34, 56)
        request = RequestFactory().get('/send_raddle_ui/6743/')

        self.assertTrue(notify_admin_site_error(request))

        text = publish_mock.call_args.kwargs['payload']['text']
        self.assertIn('Время: 23.09.2026 12:34:56', text)

    @override_settings(SITE_DEPLOY_VERSION='salad-alert-test')
    @patch('games.ops.telegram.bot.notify.send_admin_message', return_value=True)
    def test_custom_salad_submission_error_is_alerted_and_deduplicated(self, send_message_mock):
        self.assertTrue(notify_admin_word_salad_submission_error(
            game_id='salad', incident_id='abc123',
        ))
        self.assertTrue(notify_admin_word_salad_submission_error(
            game_id='salad', incident_id='def456',
        ))
        send_message_mock.assert_called_once()
        text = send_message_mock.call_args.args[0]
        self.assertIn('missing_or_invalid_offer_share', text)
        self.assertIn('Инцидент: <code>abc123</code>', text)
        self.assertIn('логе приложения', text)

    @patch('games.ops.telegram.bot.alerts.boto3.client')
    def test_custom_salad_submission_error_is_published_to_integrations_queue(self, client_mock):
        with patch.dict('os.environ', {'INTEGRATIONS_SQS_QUEUE_URL': 'https://sqs.example/alerts'}):
            from games.ops.telegram.bot.alerts import publish_word_salad_submission_error

            self.assertTrue(publish_word_salad_submission_error(
                game_id='salad', incident_id='abc123',
            ))
        payload = client_mock.return_value.send_message.call_args.kwargs
        self.assertEqual(payload['QueueUrl'], 'https://sqs.example/alerts')
        self.assertIn('telegram.admin_alert', payload['MessageBody'])
        self.assertIn('abc123', payload['MessageBody'])

    @override_settings(INSTANCE_ID='green-test-instance', SITE_DEPLOY_VERSION='deploy-test')
    @patch('games.ops.telegram.bot.notify.publish_admin_alert', return_value=True)
    def test_site_error_notification_includes_safe_diagnostic_context(self, publish_mock):
        request = RequestFactory().post('/send_attempt/226/', data={'text': 'secret-answer'})
        request.interoves_request_id = 'telegram-diagnostic-id'
        request._interoves_elapsed_ms = 123.4

        with patch.dict('os.environ', {'INTEROVES_ENVIRONMENT': 'green'}, clear=False):
            self.assertTrue(notify_admin_site_error(request, exception=RuntimeError('database down')))

        text = publish_mock.call_args.kwargs['payload']['text']
        self.assertIn('Тип: необработанное исключение', text)
        self.assertIn('Request ID: <code>telegram-diagnostic-id</code>', text)
        self.assertIn(
            'Инстанс: <code>green-test-instance</code> · среда <code>green</code> · версия <code>deploy-test</code>',
            text,
        )
        self.assertIn('Время обработки: 123 мс', text)
        self.assertIn('RuntimeError: database down', text)
        self.assertNotIn('secret-answer', text)

    def test_format_payment_message(self):
        ticket = TicketRequest.objects.create(team=self.team, tickets=2, money=4000, status='Accepted')
        text = format_payment_message(ticket, 'payment.succeeded')
        self.assertIn('Оплата', text)
        self.assertIn('TG Team', text)

    def test_announcement_messages_include_links(self):
        self.game.game_url = (
            'https://docs.google.com/document/d/conditions/edit?usp=sharing&editor=1'
        )
        start = format_game_start_announcement(self.game)
        self.assertIn('Начали!', start)
        self.assertIn('Сайт', start)
        self.assertIn(
            '<a href="https://docs.google.com/document/d/conditions/edit?usp=sharing&amp;editor=1">Условия</a>',
            start,
        )
        self.assertIn(
            '<a href="https://docs.google.com/document/d/conditions/copy?usp=sharing&amp;editor=1">Скопировать условия</a>',
            start,
        )
        end_soon = format_game_end_soon_announcement(self.game)
        self.assertIn('30 минут', end_soon)
        end_soon_15 = format_game_end_soon_15_announcement(self.game)
        self.assertIn('15 минут', end_soon_15)
        end = format_game_end_announcement(self.game)
        self.assertIn('завершилась', end)
        self.assertIn('answers', end)
        self.assertNotIn('Таблица результатов', end)

    def test_end_soon_announcements_use_genitive_for_desyatochka(self):
        self.game.no_html_name = 'Десяточка 174'
        self.game.save(update_fields=['no_html_name'])

        for formatter in (
            format_game_end_soon_announcement,
            format_game_end_soon_15_announcement,
        ):
            self.assertIn('До конца Десяточки 174', formatter(self.game))

    def test_day_before_announcement_uses_explicit_game_url(self):
        text = format_game_day_before_announcement(self.game)
        self.assertIn('https://interoves.com/games/tg_test_game/', text)
        self.assertNotIn('Открыть игру', text)

    def test_start_does_not_offer_copy_for_non_google_conditions(self):
        self.game.game_url = 'https://example.com/conditions/editor-notes'

        start = format_game_start_announcement(self.game)

        self.assertIn('Условия', start)
        self.assertNotIn('Скопировать условия', start)

    def test_every_registration_is_announced_with_the_total(self):
        from games.models import Registration, Team

        Registration.objects.create(team=Team.objects.create(name='first_reg_team'), game=self.game)
        team = Team.objects.create(name='second_reg_team', visible_name='Вторые')
        with patch('games.ops.telegram.channels.scheduling.send_admin_message', return_value=True) as send:
            with self.captureOnCommitCallbacks(execute=True):
                Registration.objects.create(team=team, game=self.game)
        text = send.call_args.args[0]
        self.assertIn('Новая регистрация на игру', text)
        self.assertIn('/support/actor/team/second_reg_team/">Вторые</a>', text)
        self.assertIn('Всего команд: <b>2</b>', text)

    def test_is_admin_chat(self):
        self.assertTrue(is_admin_chat('12345'))
        self.assertFalse(is_admin_chat('-100999'))

    @patch('games.ops.telegram.channels.scheduling.send_announce_message')
    @patch('games.ops.telegram.channels.scheduling.send_admin_message')
    def test_process_game_announcements_start(self, _admin_mock, announce_mock):
        from games.ops.telegram.channels.scheduling import process_game_announcements

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
    @override_settings(TELEGRAM_WEBHOOK_SECRET='')
    def test_webhook_disabled_without_secret(self):
        response = self.client.post('/telegram/webhook/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 503)

    def test_webhook_rejects_bad_secret(self):
        response = self.client.post('/telegram/webhook/wrong/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    @patch('games.ops.telegram.transport.webhook.send_message')
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
        self.assertIn('Команды админ-чата', send_message_mock.call_args.args[1])

    def _reply_to_bot(self, original_text, text, *, chat_id=12345, sender_id=777, edited=False):
        message = {
            'chat': {'id': chat_id, 'type': 'group'},
            'from': {'id': sender_id},
            'text': text,
            'reply_to_message': {'from': {'id': 1, 'is_bot': True}, 'text': original_text},
        }
        return self.client.post(
            '/telegram/webhook/secret/',
            data={'edited_message' if edited else 'message': message},
            content_type='application/json',
        )

    def _report(self, **fields):
        _ensure_reference_rows()
        game, _ = Game.objects.get_or_create(id='tg_reply_game', defaults={'name': 'Reply Game', 'author': 'a'})
        task = Task.objects.create(task_group=TaskGroup.objects.create(label='reply'), number='1', text='t')
        return BugReport.objects.create(game=game, task=task, **fields)

    def _report_with_author(self):
        from games.models import Profile

        user = User.objects.create(username='reporter')
        Profile.objects.create(user=user, first_name='Рита', last_name='Репорт')
        admin = User.objects.create(username='admin_in_tg')
        Profile.objects.create(user=admin, first_name='Админ', last_name='А', telegram_user_id=777)
        return self._report(user=user, text='Не грузится картинка'), admin

    @patch('games.ops.telegram.bot.replies.send_message')
    def test_reply_to_bug_message_becomes_admin_reply(self, send_message_mock):
        from games.models import BugReportMessage

        report, admin = self._report_with_author()
        with patch('games.notifications.service.notify') as notify_mock, \
                self.captureOnCommitCallbacks(execute=True):
            response = self._reply_to_bot('🐞 Баг #{}\n\nИгра: …'.format(report.pk), 'Починили, спасибо!')
        self.assertEqual(response.status_code, 200)
        reply = BugReportMessage.objects.get(report=report, author_role=BugReportMessage.ROLE_ADMIN)
        self.assertEqual(reply.text, 'Починили, спасибо!')
        self.assertEqual(reply.author_user, admin)
        notify_mock.assert_called_once()
        self.assertIn('отправлен автору', send_message_mock.call_args.args[1])

    @patch('games.ops.telegram.bot.replies.send_message')
    def test_reply_follows_the_bot_header_not_the_players_text(self, _send_message_mock):
        from games.models import BugReportMessage

        report, _admin = self._report_with_author()
        other = self._report(text='другой', user=report.user)
        self._reply_to_bot('💬 Ответ по багу #{} от автора\n\n🐞 Баг #{}'.format(report.pk, other.pk), 'Ок')
        self.assertTrue(BugReportMessage.objects.filter(report=report, author_role='admin').exists())
        self.assertFalse(BugReportMessage.objects.filter(report=other, author_role='admin').exists())

    @patch('games.ops.telegram.bot.replies.send_message')
    def test_reply_is_ignored_outside_admin_chat_and_on_edits(self, _send_message_mock):
        from games.models import BugReportMessage

        report, _admin = self._report_with_author()
        header = '🐞 Баг #{}'.format(report.pk)
        with patch('games.ops.telegram.transport.webhook.send_message'):
            self._reply_to_bot(header, 'чужой чат', chat_id=-100999)
        self._reply_to_bot(header, 'правка', edited=True)
        self.assertFalse(BugReportMessage.objects.filter(report=report, author_role='admin').exists())

    @patch('games.ops.telegram.bot.replies.send_message')
    def test_reply_to_anonymous_report_explains_it_cannot_be_delivered(self, send_message_mock):
        from games.models import BugReportMessage

        report = self._report(anon_key='anon-key-1', text='без входа')
        self._reply_to_bot('🐞 Баг #{}'.format(report.pk), 'ответ')
        self.assertFalse(BugReportMessage.objects.filter(report=report, author_role='admin').exists())
        self.assertIn('не вошёл в аккаунт', send_message_mock.call_args.args[1])

    @patch('games.ops.telegram.transport.webhook.send_message')
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

    def test_stuck_command_uses_attempt_manager_and_team_ids(self):
        task_group = TaskGroup.objects.create(label='stuck tg')
        task = Task.objects.create(task_group=task_group, number='1', text='task')
        GameTaskGroup.objects.create(
            game=self.game, task_group=task_group, number='1', name='Section',
        )
        team = Team.objects.create(name='stuck_team', visible_name='Stuck Team')
        Registration.objects.create(game=self.game, team=team)
        Attempt.manager.create(
            game=self.game, task=task, team=team, text='ok', status='Ok',
        )

        text = handle_admin_command('/stuck {}'.format(self.game.pk))

        self.assertIn('Все активные команды с недавним прогрессом.', text)
        self.assertNotIn('Stuck Team', text)


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
