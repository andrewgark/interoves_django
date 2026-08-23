from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from zoneinfo import ZoneInfo

from games.daily_transitions import next_daily_content_transition


UTC = ZoneInfo('UTC')
MOSCOW = ZoneInfo('Europe/Moscow')


class DailyContentTransitionTests(TestCase):
    def game(self, game_id, tag, start):
        return SimpleNamespace(id=game_id, tags={tag: start})

    def test_ladder_uses_next_existing_unpublished_number(self):
        game = self.game('ladder', 'ladder_publish_start', '2026-08-20')
        now = datetime(2026, 8, 20, 20, 30, tzinfo=UTC)

        transition = next_daily_content_transition(game, ['1', '3', '2'], now=now)

        self.assertEqual(
            transition,
            datetime(2026, 8, 21, 0, 0, tzinfo=MOSCOW),
        )

    def test_alphabetty_returns_none_without_future_existing_content(self):
        game = self.game('alphabetty', 'alphabetty_publish_start', '2026-08-20')
        now = datetime(2026, 8, 21, 21, 30, tzinfo=UTC)

        transition = next_daily_content_transition(game, ['1', '2'], now=now)

        self.assertIsNone(transition)

    def test_week_task_uses_next_week_boundary(self):
        game = self.game('week_task', 'week_task_publish_start', '2026-08-17')
        now = datetime(2026, 8, 23, 20, 30, tzinfo=UTC)

        transition = next_daily_content_transition(game, ['1', '2'], now=now)

        self.assertEqual(
            transition,
            datetime(2026, 8, 24, 0, 0, tzinfo=MOSCOW),
        )

    def test_word_salad_uses_next_existing_unpublished_number(self):
        game = self.game('salad', 'word_salad_publish_start', '2026-08-23')
        now = datetime(2026, 8, 23, 20, 30, tzinfo=UTC)

        transition = next_daily_content_transition(game, ['1', '3', '2'], now=now)

        self.assertEqual(
            transition,
            datetime(2026, 8, 24, 0, 0, tzinfo=MOSCOW),
        )

    def test_non_daily_game_has_no_transition(self):
        game = SimpleNamespace(id='ordinary', tags={})
        self.assertIsNone(next_daily_content_transition(game, ['1']))
