from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from games.models import Game, GameTaskGroup, Project, TaskGroup
from games.site.pages.game.progress import _player_visible_task_group_links
from games.site.pages.hub import link_cache

MOSCOW = ZoneInfo('Europe/Moscow')


@override_settings(HUB_LINK_CACHE_SECONDS=60)
class HubLinkCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        project, _ = Project.objects.get_or_create(id='sections')
        tag = 'ladder_publish_start'
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'ladder', 'author': 'test', 'project': project,
                'rules': None, 'tournament_rules': None,
            },
        )
        Game.objects.filter(pk='ladder').update(
            project=project, tags={tag: '2026-09-01T00:00:00+03:00'},
        )
        cls.game.refresh_from_db()
        for day in range(1, 6):
            tg = TaskGroup.objects.create(label='hub-cache-{}'.format(day))
            GameTaskGroup.objects.create(
                game=cls.game, number=str(day), task_group=tg, name=str(day),
            )

    def setUp(self):
        link_cache.clear()
        self.addCleanup(link_cache.clear)

    def test_links_are_read_once_until_a_placement_changes(self):
        first = link_cache.game_links(self.game)
        with self.assertNumQueries(0):
            self.assertIs(link_cache.game_links(self.game), first)

        tg = TaskGroup.objects.create(label='hub-cache-6')
        GameTaskGroup.objects.create(game=self.game, number='6', task_group=tg, name='6')
        self.assertEqual(
            [link.number for link in link_cache.game_links(self.game)],
            ['1', '2', '3', '4', '5', '6'],
        )

    def test_release_filter_follows_the_clock_not_the_cache(self):
        day3 = datetime(2026, 9, 3, 12, 0, tzinfo=MOSCOW)
        day4 = datetime(2026, 9, 4, 12, 0, tzinfo=MOSCOW)
        with patch('django.utils.timezone.now', return_value=day3):
            self.assertEqual(link_cache.published_numbers(self.game), {'1', '2', '3'})
            expected = [link.number for link in _player_visible_task_group_links(self.game)]
            link_cache.clear()
            self.assertEqual(
                [link.number for link in link_cache.visible_game_links(self.game)],
                expected,
            )
        with patch('django.utils.timezone.now', return_value=day4):
            with self.assertNumQueries(0):
                self.assertEqual(
                    link_cache.published_numbers(self.game), {'1', '2', '3', '4'},
                )
                self.assertEqual(
                    [link.number for link in link_cache.visible_game_links(self.game)],
                    ['4', '3', '2', '1'],
                )
