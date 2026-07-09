from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.ladder_daily import (
    LADDER_PUBLISH_START_TAG,
    current_ladder_number,
    get_ladder_hub_context,
    is_ladder_number_published,
    ladder_number_for_date,
    ladder_publish_at,
    ladder_publish_start,
)
from games.models import Game, Project, Task, TaskGroup


class LadderDailyLogicTests(SimpleTestCase):
    def _game(self, start='2026-07-08T00:00:00+03:00'):
        return Game(tags={LADDER_PUBLISH_START_TAG: start})

    def test_number_for_date(self):
        game = self._game()
        self.assertIsNone(ladder_number_for_date(game, datetime(2026, 7, 7, tzinfo=ZoneInfo('Europe/Moscow')).date()))
        self.assertEqual(ladder_number_for_date(game, datetime(2026, 7, 8, tzinfo=ZoneInfo('Europe/Moscow')).date()), 1)
        self.assertEqual(ladder_number_for_date(game, datetime(2026, 7, 9, tzinfo=ZoneInfo('Europe/Moscow')).date()), 2)

    def test_publish_at(self):
        game = self._game()
        pub1 = ladder_publish_at(game, 1)
        pub2 = ladder_publish_at(game, 2)
        self.assertEqual(pub1.date().isoformat(), '2026-07-08')
        self.assertEqual(pub2.date().isoformat(), '2026-07-09')

    def test_is_published(self):
        game = self._game()
        before = datetime(2026, 7, 7, 23, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        after = datetime(2026, 7, 8, 1, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        self.assertFalse(is_ladder_number_published(game, 1, before))
        self.assertTrue(is_ladder_number_published(game, 1, after))
        self.assertFalse(is_ladder_number_published(game, 2, after))

    def test_hub_context_today(self):
        game = self._game()
        now = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        ctx = get_ladder_hub_context(game, published_numbers={'1', '2'}, now=now)
        self.assertEqual(ctx['ladder_cta_number'], '1')
        self.assertTrue(ctx['ladder_is_today'])
        self.assertEqual(ctx['ladder_status'], 'today')

    def test_hub_context_latest_when_today_missing(self):
        game = self._game()
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        ctx = get_ladder_hub_context(game, published_numbers={'1'}, now=now)
        self.assertEqual(ctx['ladder_cta_number'], '1')
        self.assertFalse(ctx['ladder_is_today'])
        self.assertEqual(ctx['ladder_status'], 'latest')


class LadderGameMigrationTests(TestCase):
    def test_ladder_game_exists_after_migration(self):
        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        self.assertEqual(game.name, 'Лесенка')
        self.assertIsNotNone(ladder_publish_start(game))


class GameTaskGroupNumberOrderTests(TestCase):
    def test_order_queryset_by_number(self):
        from games.models import GameTaskGroup, Project

        Project.objects.get_or_create(id='sections')
        project = Project.objects.get(id='sections')
        game, _ = Game.objects.get_or_create(
            id='ladder_sort_test',
            defaults={'name': 't', 'author': 't', 'project': project},
        )
        for n in (10, 2, 1, 20):
            tg = TaskGroup.objects.create(label=f't{n}')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=str(n), name=f'#{n}')
        ordered = GameTaskGroup.order_queryset_by_number(
            GameTaskGroup.objects.filter(game=game)
        )
        self.assertEqual(list(ordered.values_list('number', flat=True)), ['1', '2', '10', '20'])
        ordered_desc = GameTaskGroup.order_queryset_by_number(
            GameTaskGroup.objects.filter(game=game), reverse=True
        )
        self.assertEqual(list(ordered_desc.values_list('number', flat=True)), ['20', '10', '2', '1'])
        game.delete()


class FilterPublishedLadderLinksTests(TestCase):
    def test_returns_queryset_compatible_with_order_queryset_by_number(self):
        from datetime import datetime
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from games import ladder_daily
        from games.ladder_daily import filter_published_ladder_links
        from games.models import GameTaskGroup

        game = Game.objects.get(id='ladder', project_id='sections')
        game.tags = {'ladder_publish_start': '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])
        GameTaskGroup.objects.filter(game=game).delete()
        for n in (1, 2):
            tg = TaskGroup.objects.create(label=f'f{n}')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=str(n), name=f'#{n}')

        qs = GameTaskGroup.objects.filter(game=game)
        before = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with patch.object(ladder_daily.timezone, 'now', return_value=before):
            published = filter_published_ladder_links(qs, game)
        self.assertTrue(hasattr(published, 'filter'))
        ordered = GameTaskGroup.order_queryset_by_number(published, reverse=True)
        self.assertEqual(list(ordered.values_list('number', flat=True)), ['1'])


class LadderSectionPageTests(TestCase):
    def test_hub_section_task_group_links_ladder_does_not_crash(self):
        from games.views.new_ui import _hub_section_task_group_links

        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        list(_hub_section_task_group_links(game))


class LadderResultsVisibilityTests(TestCase):
    def test_results_headers_exclude_unpublished_ladders(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from games.models import GameTaskGroup, Project
        from games.views.new_ui import _results_table_headers_context

        Project.objects.get_or_create(id='sections')
        project = Project.objects.get(id='sections')
        game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Лесенка',
                'author': 'Interoves',
                'project': project,
                'is_ready': True,
                'tags': {'ladder_publish_start': '2026-07-08T00:00:00+03:00'},
            },
        )
        game.tags = {'ladder_publish_start': '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        for n in (1, 2, 3):
            tg = TaskGroup.objects.create(label=f'l{n}')
            Task.objects.create(task_group=tg, number='1', text='x')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=str(n), name=f'#{n}')

        before = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with self.settings(TIME_ZONE='Europe/Moscow'):
            from unittest.mock import patch
            from games import ladder_daily
            with patch.object(ladder_daily.timezone, 'now', return_value=before):
                headers = _results_table_headers_context(game)['task_groups']
        self.assertEqual([h.number for h in headers], ['1'])

        after = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with patch.object(ladder_daily.timezone, 'now', return_value=after):
            headers = _results_table_headers_context(game)['task_groups']
        self.assertEqual([h.number for h in headers], ['1', '2'])
