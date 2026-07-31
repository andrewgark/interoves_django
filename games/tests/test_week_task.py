"""Задание недели: пул units, календарь, support generate/buffer."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup
from games.support.services.week_tasks import (
    WeekTaskSupportError,
    ensure_future_buffer,
    generate_more,
    list_week_task_rows,
    reorder_week_tasks,
    set_publish_start,
)
from games.week_task_pool import (
    enumerate_units_for_gtg,
    pick_random_units,
    scheduled_exclude_keys,
)
from games.week_task_weekly import (
    WEEK_TASK_GAME_ID,
    WEEK_TASK_PUBLISH_START_TAG,
    current_week_task_number,
    get_week_task_hub_context,
    is_week_task_number_published,
    week_task_publish_at,
)

MOSCOW = ZoneInfo('Europe/Moscow')


def _ensure_projects():
    Project.objects.get_or_create(id='main')
    Project.objects.get_or_create(id='sections')
    CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


def _ensure_week_task_game(**tag_overrides):
    _ensure_projects()
    tags = {WEEK_TASK_PUBLISH_START_TAG: '2026-08-03T00:00:00+03:00'}
    tags.update(tag_overrides)
    game, _ = Game.objects.update_or_create(
        id=WEEK_TASK_GAME_ID,
        defaults={
            'name': 'Задание недели',
            'author': 'Interoves',
            'project_id': 'sections',
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': tags,
        },
    )
    GameTaskGroup.objects.filter(game=game).delete()
    return game


def _make_des_game(game_id='des100'):
    _ensure_projects()
    game, _ = Game.objects.update_or_create(
        id=game_id,
        defaults={
            'name': f'Десяточка {game_id[3:]}',
            'outside_name': f'Десяточка {game_id[3:]}',
            'author': 'Interoves',
            'project_id': 'main',
            'is_ready': True,
            'is_playable': True,
        },
    )
    return game


def _add_circle(game, *, number, name, view='default', task_numbers, label=''):
    checker = CheckerType.objects.get(id='equals_with_possible_spaces')
    tg = TaskGroup.objects.create(
        label=label or name,
        checker=checker,
        points=1,
        view=view,
    )
    for num in task_numbers:
        Task.objects.create(
            task_group=tg,
            number=str(num),
            task_type='proportions' if view == 'proportions' else 'default',
            checker=checker,
            answer='ok',
            text=f'task {num}',
            points=1,
            is_removed=False,
        )
    return GameTaskGroup.objects.create(
        game=game,
        task_group=tg,
        number=str(number),
        name=name,
    )


class WeekTaskCalendarTests(TestCase):
    def setUp(self):
        self.game = _ensure_week_task_game()

    def test_publish_every_monday(self):
        pub1 = week_task_publish_at(self.game, 1)
        pub2 = week_task_publish_at(self.game, 2)
        self.assertEqual(pub1.date().isoformat(), '2026-08-03')
        self.assertEqual(pub1.weekday(), 0)
        self.assertEqual(pub2.date().isoformat(), '2026-08-10')
        self.assertEqual((pub2 - pub1).days, 7)

    def test_current_number_and_gate(self):
        before = datetime(2026, 8, 2, 23, 0, tzinfo=MOSCOW)
        self.assertIsNone(current_week_task_number(self.game, before))
        self.assertFalse(is_week_task_number_published(self.game, 1, before))

        monday = datetime(2026, 8, 3, 0, 0, tzinfo=MOSCOW)
        self.assertEqual(current_week_task_number(self.game, monday), 1)
        self.assertTrue(is_week_task_number_published(self.game, 1, monday))
        self.assertFalse(is_week_task_number_published(self.game, 2, monday))

        week2 = datetime(2026, 8, 10, 12, 0, tzinfo=MOSCOW)
        self.assertEqual(current_week_task_number(self.game, week2), 2)

    def test_missing_publish_start_keeps_closed(self):
        self.game.tags = {}
        self.game.save(update_fields=['tags'])
        self.assertFalse(is_week_task_number_published(self.game, 1))
        self.assertFalse(is_week_task_number_published(self.game, 'abc'))

    def test_hub_empty_after_start_without_content(self):
        after = datetime(2026, 8, 10, 12, 0, tzinfo=MOSCOW)
        ctx = get_week_task_hub_context(self.game, published_numbers=set(), now=after)
        self.assertEqual(ctx['week_task_status'], 'empty')
        self.assertIsNone(ctx['week_task_play_url'])

    def test_hub_coming_soon_before_start(self):
        before = datetime(2026, 8, 1, 12, 0, tzinfo=MOSCOW)
        ctx = get_week_task_hub_context(self.game, published_numbers=set(), now=before)
        self.assertEqual(ctx['week_task_status'], 'coming_soon')


class WeekTaskPoolSplitTests(TestCase):
    def setUp(self):
        self.des = _make_des_game('des200')

    def test_sequences_split_by_major(self):
        gtg = _add_circle(
            self.des,
            number=1,
            name='Последовательности',
            task_numbers=['1.1', '1.2', '1.3', '2', '3.1', '3.2'],
        )
        units = enumerate_units_for_gtg(gtg)
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0].major, '1')
        self.assertEqual(units[0].task_numbers, ('1.1', '1.2', '1.3'))
        self.assertEqual(units[1].major, '2')
        self.assertEqual(units[1].task_numbers, ('2',))
        self.assertEqual(units[2].major, '3')
        self.assertEqual(units[2].task_numbers, ('3.1', '3.2'))
        self.assertEqual(units[0].display_name(), 'Последовательности · 1')

    def test_proportions_split(self):
        gtg = _add_circle(
            self.des,
            number=2,
            name='Соотношения',
            view='proportions',
            task_numbers=['1.1', '1.2', '2.1'],
        )
        units = enumerate_units_for_gtg(gtg)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].task_numbers, ('1.1', '1.2'))
        self.assertEqual(units[1].task_numbers, ('2.1',))

    def test_ordinary_circle_is_one_unit(self):
        gtg = _add_circle(
            self.des,
            number=3,
            name='Обычное',
            task_numbers=['1', '2', '3'],
        )
        units = enumerate_units_for_gtg(gtg)
        self.assertEqual(len(units), 1)
        self.assertIsNone(units[0].task_numbers)
        self.assertIsNone(units[0].major)

    def test_excluded_genres(self):
        for i, name in enumerate(('Замены', 'Стены', 'Палиндромы'), start=20):
            gtg = _add_circle(
                self.des,
                number=i,
                name=name,
                task_numbers=['1'],
            )
            self.assertEqual(enumerate_units_for_gtg(gtg), [])


class WeekTaskSupportTests(TestCase):
    def setUp(self):
        self.week_game = _ensure_week_task_game()
        self.des = _make_des_game('des300')
        # Несколько units в пуле
        _add_circle(
            self.des,
            number=1,
            name='Последовательности',
            task_numbers=['1.1', '1.2', '2', '3.1'],
        )
        _add_circle(
            self.des,
            number=2,
            name='Обычное А',
            task_numbers=['1', '2'],
        )
        des2 = _make_des_game('des301')
        _add_circle(
            des2,
            number=1,
            name='Обычное Б',
            task_numbers=['1'],
        )
        _add_circle(
            des2,
            number=2,
            name='Соотношения',
            view='proportions',
            task_numbers=['1.1', '2.1', '3.1'],
        )

    def test_generate_clones_and_excludes(self):
        result = generate_more(3)
        self.assertEqual(result['created_count'], 3)
        rows = list_week_task_rows()
        self.assertEqual(len(rows), 3)
        # Снимки — отдельные TaskGroup
        for row in rows:
            tg = TaskGroup.objects.get(pk=row.task_group_id)
            self.assertTrue((tg.label or '').startswith('week_task:'))
            self.assertIn('week_task_source', tg.tags or {})
            self.assertGreaterEqual(tg.tasks.visible().count(), 1)

        exclude = scheduled_exclude_keys(week_task_game=self.week_game)
        self.assertEqual(len(exclude), 3)
        more = pick_random_units(10, exclude=exclude)
        for u in more:
            self.assertNotIn(u.exclude_key, exclude)

    def test_buffer_and_reorder_lock(self):
        generate_more(2)
        buf = ensure_future_buffer(5)
        self.assertEqual(buf['added'], 3)
        rows = list_week_task_rows()
        self.assertGreaterEqual(sum(1 for r in rows if not r.is_published), 5)

        set_publish_start('2020-08-03')  # Monday
        rows = list_week_task_rows()
        published = [r for r in rows if r.is_published]
        self.assertTrue(published)
        ids = [r.link_id for r in rows]
        bad = ids[1:] + ids[:1]
        with self.assertRaises(WeekTaskSupportError):
            reorder_week_tasks(bad)

    def test_buffer_targets_calendar_week_not_unpublished_count(self):
        """Старт в прошлом + мало слотов: догоняем current+target, не жрём пул зря."""
        set_publish_start('2026-07-06')  # Monday; week1
        self.week_game.refresh_from_db()
        generate_more(1)  # only №1
        # 2026-08-03 = week 5 relative to 2026-07-06
        now = datetime(2026, 8, 3, 12, 0, tzinfo=MOSCOW)
        self.assertEqual(current_week_task_number(self.week_game, now), 5)
        buf = ensure_future_buffer(3, now=now)
        # need max_number >= 5+3=8, had 1 → add 7
        self.assertEqual(buf['added'], 7)
        rows = list_week_task_rows(now=now)
        self.assertEqual(max(r.number for r in rows), 8)
        future = [r for r in rows if not r.is_published]
        self.assertEqual(len(future), 3)
        self.assertEqual(buf['future'], 3)
        # Повторный вызов ничего не добавляет
        buf2 = ensure_future_buffer(3, now=now)
        self.assertEqual(buf2['added'], 0)

    def test_publish_start_must_be_monday(self):
        with self.assertRaises(WeekTaskSupportError):
            set_publish_start('2026-08-04')  # Tuesday
