from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone

from games.ladder_daily import LADDER_PUBLISH_START_TAG
from games.section_hub import (
    get_desyatochki_hub_context,
    get_ladder_section_hub_card,
    get_training_section_hub_context,
)


class _FakeGame:
    def __init__(self, game_id, links=None):
        self.id = game_id
        self.links = links or []


class _FakeLink:
    def __init__(self, number):
        self.number = str(number)


class TrainingSectionHubContextTests(SimpleTestCase):
    def test_latest_task_group(self):
        game = _FakeGame('walls', [_FakeLink(5), _FakeLink(3)])

        def fake_newest(_game):
            return game.links

        import games.section_hub as sh
        orig = sh._newest_task_group_links
        sh._newest_task_group_links = fake_newest
        try:
            ctx = get_training_section_hub_context(game)
        finally:
            sh._newest_task_group_links = orig

        self.assertEqual(ctx['cta_number'], '5')
        self.assertEqual(ctx['cta_label'], 'Последняя стена')
        self.assertEqual(ctx['play_url'], '/games/walls/5/')
        self.assertFalse(ctx['is_today'])


class LadderSectionHubCardTests(SimpleTestCase):
    def _game(self, start='2026-07-08T00:00:00+03:00'):
        return type('G', (), {'id': 'ladder'})()

    def test_today_label(self):
        game = type('G', (), {'id': 'ladder', 'tags': {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}})()
        now = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        card = get_ladder_section_hub_card(game, published_numbers={'1'}, now=now)
        self.assertTrue(card['is_today'])
        self.assertEqual(card['cta_label'], 'Сегодняшняя лесенка')

    def test_latest_label(self):
        game = type('G', (), {'id': 'ladder', 'tags': {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}})()
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        card = get_ladder_section_hub_card(game, published_numbers={'1'}, now=now)
        self.assertFalse(card['is_today'])
        self.assertEqual(card['cta_label'], 'Последняя лесенка')


class DesyatochkiHubContextTests(SimpleTestCase):
    def _game(self, game_id, start_iso):
        return type('G', (), {
            'id': game_id,
            'start_time': datetime.fromisoformat(start_iso),
        })()

    def test_today_game(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('g2', '2026-07-10T18:00:00+03:00'),
            self._game('g1', '2026-07-03T18:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertTrue(ctx['is_today'])
        self.assertEqual(ctx['cta_label'], 'Сегодняшняя Десяточка')
        self.assertEqual(ctx['play_url'], '/games/g2/')

    def test_latest_game(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-03T18:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertFalse(ctx['is_today'])
        self.assertEqual(ctx['cta_label'], 'Последняя Десяточка')
        self.assertIsNone(ctx['announced_game'])

    def test_announced_future_game(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('future', '2026-07-20T18:00:00+03:00'),
            self._game('past', '2026-07-03T18:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertEqual(ctx['announced_game'].id, 'future')
        self.assertEqual(ctx['announced_games'], [ctx['announced_game']])
        self.assertEqual(ctx['play_url'], '/games/future/')

    def test_started_game_not_announced(self):
        now = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-10T18:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertIsNone(ctx['announced_game'])
        self.assertEqual(ctx['announced_games'], [])
