import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from games.alphabetty_daily import ALPHABETTY_PUBLISH_START_TAG
from games.ladder_daily import LADDER_PUBLISH_START_TAG
from games.models import Game, GameTaskGroup, Project, Task, TaskGroup
from games.telegram.daily_review import build_daily_review, process_daily_review_tick
from games.telegram.models import TelegramDailyReview
from games.tests.test_raddle import PARIS_LADDER
from games.word_salad_daily import WORD_SALAD_PUBLISH_START_TAG
from games.week_task_weekly import WEEK_TASK_PUBLISH_START_TAG


MOSCOW = ZoneInfo('Europe/Moscow')


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='12345',
    SITE_BASE_URL='https://interoves.com',
)
class TelegramDailyReviewTests(TestCase):
    def setUp(self):
        Project.objects.get_or_create(id='sections')
        start = '2026-08-28T00:00:00+03:00'
        self._create_edition(
            game_id='ladder',
            game_name='Лесенка',
            start_tag=LADDER_PUBLISH_START_TAG,
            start=start,
            task_type='raddle',
            checker_data=json.dumps(PARIS_LADDER, ensure_ascii=False),
            answer='\n'.join(PARIS_LADDER['words']),
            text='Из города в город',
            task_tags={'author': 'Редактор'},
        )
        self._create_edition(
            game_id='alphabetty',
            game_name='Алфавитка',
            start_tag=ALPHABETTY_PUBLISH_START_TAG,
            start=start,
            task_type='alphabetty',
            checker_data='АБРИКОС',
            answer='АБРИКОС',
        )
        self._create_edition(
            game_id='salad',
            game_name='Салатик',
            start_tag=WORD_SALAD_PUBLISH_START_TAG,
            start=start,
            task_type='word_salad',
            checker_data=json.dumps({
                'grid': list('АБВГДЕЖЗИЙКЛМНОП'),
                'words': ['АБВГ', 'ДЕЖЗ', 'ИЙКЛ', 'МНОП'],
            }, ensure_ascii=False),
            answer='',
            text='Буквы',
        )
        self.now = datetime(2026, 8, 27, 22, 0, tzinfo=MOSCOW)

    def _create_edition(
        self,
        *,
        game_id,
        game_name,
        start_tag,
        start,
        task_type,
        checker_data,
        answer,
        text='',
        task_tags=None,
    ):
        game, _created = Game.objects.update_or_create(
            id=game_id,
            defaults={
                'name': game_name,
                'author': 'test',
                'project_id': 'sections',
                'tags': {start_tag: start},
            },
        )
        GameTaskGroup.objects.filter(game=game).delete()
        group = TaskGroup.objects.create(label='{}:1'.format(game_id))
        task = Task.objects.create(
            task_group=group,
            number='1',
            task_type=task_type,
            checker_data=checker_data,
            answer=answer,
            text=text,
            tags=task_tags or {},
        )
        GameTaskGroup.objects.create(
            game=game,
            task_group=group,
            number='1',
            name='{} №1'.format(game_name),
        )
        return task

    def test_builds_compact_review_with_spoiler_answers_and_editor_buttons(self):
        text, keyboard = build_daily_review(self.now.date().replace(day=28))

        self.assertIn('пятница, 28 августа 2026', text)
        self.assertIn('Лесенка №1', text)
        self.assertIn('Житель ____а', text)
        self.assertIn('<tg-spoiler>ПАРИЖАНИН</tg-spoiler>', text)
        self.assertIn('Алфавитка №1', text)
        self.assertIn('<tg-spoiler>АБРИКОС</tg-spoiler>', text)
        self.assertIn('Салатик №1', text)
        self.assertIn('<pre>А Б В Г', text)
        self.assertIn('<tg-spoiler>АБВГ · ДЕЖЗ · ИЙКЛ · МНОП</tg-spoiler>', text)
        self.assertIn('✅ Все 3 выпуска на месте.', text)
        self.assertLessEqual(len(text), 4096)

        buttons = keyboard['inline_keyboard'][0]
        self.assertEqual([button['text'] for button in buttons], [
            '🪜 Лесенка', '🔤 Алфавитка', '🥗 Салатик',
        ])
        self.assertEqual(buttons[0]['url'], 'https://interoves.com/support/ladders/')

    @patch('games.telegram.daily_review.send_admin_message', return_value=True)
    def test_tick_sends_once_for_tomorrow(self, send_mock):
        first = process_daily_review_tick(now=self.now)
        second = process_daily_review_tick(now=self.now.replace(minute=1))

        self.assertEqual(first, {'sent': 1, 'skipped': 0})
        self.assertEqual(second, {'sent': 0, 'skipped': 1})
        self.assertEqual(send_mock.call_count, 1)
        self.assertTrue(TelegramDailyReview.objects.filter(review_date='2026-08-28').exists())
        sent_text = send_mock.call_args.args[0]
        self.assertIn('Задания на завтра', sent_text)
        self.assertIn('reply_markup', send_mock.call_args.kwargs)

    @patch('games.telegram.daily_review.send_admin_message', return_value=False)
    def test_failed_send_releases_marker_for_next_minute_retry(self, send_mock):
        first = process_daily_review_tick(now=self.now)
        second = process_daily_review_tick(now=self.now.replace(minute=1))

        self.assertEqual(first['sent'], 0)
        self.assertEqual(second['sent'], 0)
        self.assertEqual(send_mock.call_count, 2)
        self.assertFalse(TelegramDailyReview.objects.exists())

    def test_missing_slot_is_an_explicit_warning(self):
        GameTaskGroup.objects.filter(game_id='salad').delete()

        text, _keyboard = build_daily_review(self.now.date().replace(day=28))

        self.assertIn('нет выпуска №1', text)
        self.assertIn('Готово выпусков: 2 из 3', text)

    def test_includes_week_task_only_on_the_eve_of_its_publication(self):
        self._create_edition(
            game_id='week_task',
            game_name='Задание недели',
            start_tag=WEEK_TASK_PUBLISH_START_TAG,
            start='2026-08-31T00:00:00+03:00',
            task_type='default',
            checker_data='ГОРОД',
            answer='МОСКВА',
            text='Назовите столицу России',
        )

        friday_text, _ = build_daily_review(self.now.date().replace(day=28))
        monday_text, monday_keyboard = build_daily_review(self.now.date().replace(day=31))

        self.assertNotIn('Задание недели №1', friday_text)
        self.assertIn('Задание недели №1', monday_text)
        self.assertIn('Назовите столицу России', monday_text)
        self.assertIn('<tg-spoiler>МОСКВА</tg-spoiler>', monday_text)
        self.assertEqual(
            monday_keyboard['inline_keyboard'][1][0]['url'],
            'https://interoves.com/support/week-tasks/',
        )

    @patch('games.telegram.daily_review.send_admin_message')
    def test_tick_ignores_time_outside_window(self, send_mock):
        result = process_daily_review_tick(now=self.now.replace(hour=21, minute=59))

        self.assertEqual(result, {'sent': 0, 'skipped': 1})
        send_mock.assert_not_called()
