from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from games.models import Game, Like, PlayerAnalyticsState, PlayerStartedGame, Task, TaskGroup
from games.ops.analytics import retention_report as rr
from games.ops.telegram.bot.reports import build_admin_report

MOSCOW = ZoneInfo('Europe/Moscow')
END = datetime(2026, 11, 20, tzinfo=MOSCOW)  # report end: Friday 00:00


class RetentionReportTests(TestCase):
    def setUp(self):
        self.game = Game.objects.get(id='salad')
        self.groups = {}

    def _group(self, number):
        if number not in self.groups:
            self.groups[number] = TaskGroup.objects.create(label=f'retention:{number}')
        return self.groups[number]

    def _start(self, anon, when, *, number=1, kind='salad', team=None):
        group = self._group(number)
        row = PlayerStartedGame.objects.create(
            game=self.game, task_group=group, game_kind=kind,
            game_instance_id=f'{kind}:{group.pk}:{anon}:{when.timestamp()}', public_game_id=f'{kind}:{number}',
            anon_key=None if team else anon, team=team,
        )
        PlayerStartedGame.objects.filter(pk=row.pk).update(started_at=when)

    def _days_ago(self, days, hour=12):
        return END - timedelta(days=days) + timedelta(hours=hour)

    def test_regular_flows(self):
        for day in (1, 2, 3):  # regular now and a week ago
            self._start('steady', self._days_ago(day))
            self._start('steady', self._days_ago(day + 7))
        for day in (8, 9, 10):  # regular a week ago, silent now
            self._start('gone', self._days_ago(day))
        for day in (8, 9, 10):  # regular a week ago, one day now
            self._start('lighter', self._days_ago(day))
        self._start('lighter', self._days_ago(2))
        for day in (1, 3, 5):  # new regular
            self._start('rising', self._days_ago(day))

        regulars, _active = rr.regulars_at(END)
        self.assertEqual(regulars, {('a', 'steady'), ('a', 'rising')})
        lines = rr.regulars_lines(END)
        self.assertIn('Постоянные (3+ дня из 7): 2 · неделю назад: 3', lines[0])
        self.assertIn('остались 1 · стали реже 1 · перестали играть 1 · новые постоянные 1', lines[1])

    def test_newcomer_cohort_returns_and_sources(self):
        self._start('back', self._days_ago(10))
        self._start('back', self._days_ago(8))
        self._start('once', self._days_ago(12))
        self._start('same-day', self._days_ago(9, hour=10))
        self._start('same-day', self._days_ago(9, hour=20))
        self._start('veteran', self._days_ago(40))
        self._start('veteran', self._days_ago(10))
        PlayerAnalyticsState.objects.create(
            anon_key='back', first_touch_at=self._days_ago(10), first_utm_source='<telegram>',
        )

        cohort = rr.newcomer_cohort(END - timedelta(days=14), END - timedelta(days=7))
        self.assertEqual(cohort, {('a', 'back'): True, ('a', 'once'): False, ('a', 'same-day'): False})

        lines = rr.newcomer_lines(END)
        self.assertIn('Новички (первая игра 8–14 дней назад): 3 · вернулись в течение недели: 33%', lines[0])
        self.assertIn('&lt;telegram&gt; 1 (100%)', lines[1])
        self.assertIn('до учёта источников 2 (0%)', lines[1])

    def test_daily_likes_show_each_games_main_puzzle(self):
        for anon in ('p1', 'p2'):
            self._start(anon, self._days_ago(1), number=79)
        self._start('p3', self._days_ago(1), number=78)
        task = Task.objects.create(task_group=self._group(79), number='1', task_type='salad', text='')
        for n in range(4):
            Like.manager.create(task=task, value=1, anon_key=f'fan{n}')
        for n in range(8):
            Like.manager.create(task=task, value=-1, anon_key=f'critic{n}')

        lines = rr.likes_lines(END - timedelta(days=1), END, weekly=False)
        self.assertEqual(lines, ['Оценки выпусков: Салатик #79: 👍4 👎8 🔴'])
        self.assertEqual(rr.likes_lines(END - timedelta(days=7), END, weekly=True),
                         ['Выпуски недели с плохими оценками: Салатик #79: 👍4 👎8 🔴'])

    @override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_ADMIN_CHAT_ID='12345')
    def test_admin_report_contains_the_section_and_survives_a_failing_part(self):
        self._start('steady', self._days_ago(1))
        text, _kind, _period = build_admin_report(now=END + timedelta(minutes=25))
        self.assertIn('📈 Удержание', text)
        self.assertIn('Постоянные (3+ дня из 7)', text)

        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        original = rr.newcomer_lines
        rr.newcomer_lines = boom
        try:
            with self.assertLogs('games.analytics.retention_report', level='ERROR'):
                lines = rr.report_lines(END - timedelta(days=1), END)
        finally:
            rr.newcomer_lines = original
        self.assertTrue(any('Постоянные' in line for line in lines))
        self.assertFalse(any('Новички' in line for line in lines))
