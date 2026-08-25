from datetime import datetime
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
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
        self.assertEqual(ctx['play_url'], '/walls/last/')
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
        self.assertEqual(card['soon_text'], 'Новая лесенка — каждый день в полночь по Москве.')
        self.assertTrue(card['wide'])

    def test_latest_label(self):
        game = type('G', (), {'id': 'ladder', 'tags': {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}})()
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        card = get_ladder_section_hub_card(game, published_numbers={'1'}, now=now)
        self.assertFalse(card['is_today'])
        self.assertEqual(card['cta_label'], 'Последняя лесенка')


class HubSectionCardTemplateTests(SimpleTestCase):
    card = {
        'id': 'section',
        'title': 'Раздел',
        'description': 'Описание',
        'play_url': '/section/1/',
        'cta_label': 'Текущее задание',
        'is_today': True,
        'today_label': 'Сегодня',
    }

    def test_daily_card_uses_green_today_cta(self):
        html = render_to_string(
            'new/partials/hub_section_card.html',
            {'card': self.card, 'highlight_daily_cta': True},
        )

        self.assertIn('new-hub-section__cta--today', html)
        self.assertIn('new-hub-section--today', html)
        self.assertIn('new-hub-section__badge-today', html)
        self.assertNotIn('new-hub-section__cta--latest', html)

    def test_non_daily_card_uses_outline_cta_even_when_current(self):
        html = render_to_string(
            'new/partials/hub_section_card.html',
            {'card': self.card, 'highlight_daily_cta': False},
        )

        self.assertNotIn('new-hub-section__cta--today', html)
        self.assertNotIn('new-hub-section--today', html)
        self.assertNotIn('new-hub-section__badge-today', html)
        self.assertIn('new-hub-section__cta--latest', html)

    def test_create_link_starts_on_new_line_for_ladder_and_alphabetty(self):
        for section_id, create_url in (
            ('ladder', '/offer_ladder/'),
            ('alphabetty', '/create_alphabetty/'),
        ):
            with self.subTest(section_id=section_id):
                card = {
                    **self.card,
                    'id': section_id,
                    'section_url': f'/{section_id}/',
                    'all_link_label': 'Все →',
                }
                html = render_to_string(
                    'new/partials/hub_section_card.html',
                    {'card': card},
                )

                self.assertIn(f'<br><a class="new-link-dotted" href="{create_url}">', html)
                self.assertNotIn('· <a class="new-link-dotted"', html)


class DesyatochkiHubContextTests(SimpleTestCase):
    def _game(self, game_id, start_iso, end_iso=None):
        start = datetime.fromisoformat(start_iso)
        if end_iso is None:
            end = start.replace(hour=min(start.hour + 4, 23))
            if end <= start:
                end = start
        else:
            end = datetime.fromisoformat(end_iso)
        return type('G', (), {
            'id': game_id,
            'start_time': start,
            'end_time': end,
        })()

    def test_today_game(self):
        now = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('g2', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00'),
            self._game('g1', '2026-07-03T18:00:00+03:00', '2026-07-03T22:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertTrue(ctx['is_today'])
        self.assertEqual(ctx['cta_label'], 'Сегодняшняя Десяточка')
        self.assertEqual(ctx['play_url'], '/games/g2/')
        self.assertEqual(ctx['announced_game'].id, 'g2')

    def test_today_game_not_started_yet_cta_points_to_latest_started(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('g2', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00'),
            self._game('g1', '2026-07-03T18:00:00+03:00', '2026-07-03T22:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertFalse(ctx['is_today'])
        self.assertEqual(ctx['cta_label'], 'Последняя Десяточка')
        self.assertEqual(ctx['play_url'], '/games/g1/')
        self.assertEqual(ctx['announced_game'].id, 'g2')

    def test_latest_game(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-03T18:00:00+03:00', '2026-07-03T22:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertFalse(ctx['is_today'])
        self.assertEqual(ctx['cta_label'], 'Последняя Десяточка')
        self.assertIsNone(ctx['announced_game'])

    def test_announced_future_game(self):
        now = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('future', '2026-07-20T18:00:00+03:00', '2026-07-20T22:00:00+03:00'),
            self._game('past', '2026-07-03T18:00:00+03:00', '2026-07-03T22:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertEqual(ctx['announced_game'].id, 'future')
        self.assertEqual(ctx['announced_games'], [ctx['announced_game']])
        self.assertEqual(ctx['play_url'], '/games/past/')
        self.assertEqual(ctx['cta_label'], 'Последняя Десяточка')
        self.assertFalse(ctx['is_today'])

    def test_started_game_still_announced(self):
        now = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertEqual(ctx['announced_game'].id, 'g1')
        self.assertEqual(ctx['announced_games'], [ctx['announced_game']])

    def test_ended_game_announced_within_a_day(self):
        now = datetime(2026, 7, 11, 10, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertEqual(ctx['announced_game'].id, 'g1')

    def test_ended_game_not_announced_after_a_day(self):
        now = datetime(2026, 7, 11, 23, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [self._game('g1', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00')]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertIsNone(ctx['announced_game'])
        self.assertEqual(ctx['announced_games'], [])

    def test_upcoming_preferred_over_live(self):
        now = datetime(2026, 7, 10, 20, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        games = [
            self._game('future', '2026-07-20T18:00:00+03:00', '2026-07-20T22:00:00+03:00'),
            self._game('live', '2026-07-10T18:00:00+03:00', '2026-07-10T22:00:00+03:00'),
        ]
        ctx = get_desyatochki_hub_context(games, now=now)
        self.assertEqual(ctx['announced_game'].id, 'future')
