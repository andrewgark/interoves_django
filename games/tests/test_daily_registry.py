from unittest import TestCase

from games.daily.registry import DAILY_GAME_REGISTRY, get_daily_game


class DailyGameRegistryTests(TestCase):
    def test_existing_daily_games_are_registered(self):
        self.assertEqual(
            [definition.game_id for definition in DAILY_GAME_REGISTRY.all()],
            ['ladder', 'alphabetty', 'salad'],
        )

    def test_definition_connects_identity_to_schedule(self):
        salad = get_daily_game('salad')

        self.assertIsNotNone(salad)
        self.assertEqual(salad.task_type, 'word_salad')
        self.assertEqual(salad.pager_label, 'салатиками')
        self.assertEqual(salad.schedule.game_id, salad.game_id)
        self.assertTrue(salad.capabilities.statistics)

    def test_unknown_game_is_not_silently_treated_as_daily(self):
        self.assertIsNone(get_daily_game('new_game_not_registered'))

    def test_require_reports_unknown_game(self):
        with self.assertRaisesRegex(KeyError, 'unknown daily game'):
            DAILY_GAME_REGISTRY.require('new_game_not_registered')

    def test_registry_rejects_duplicate_ids(self):
        definition = DAILY_GAME_REGISTRY.require('ladder')
        from games.daily.registry import DailyGameRegistry

        with self.assertRaises(ValueError):
            DailyGameRegistry((definition, definition))
