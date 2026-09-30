from dataclasses import replace
from unittest import TestCase

from games.daily.registry import (
    DAILY_GAME_REGISTRY,
    DailyGameRegistry,
    get_daily_game,
)
from games.daily.board import DAILY_BOARD_ADAPTERS
from games.daily.results import get_daily_results_adapter
from games.daily.share import DAILY_SHARE_ADAPTERS


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
        self.assertEqual(salad.task_results_variant, 'salad_words')
        self.assertEqual(salad.board_adapter_key, 'word_salad')
        self.assertEqual(salad.share_adapter_key, 'salad')
        self.assertEqual(salad.statistics_adapter_key, 'salad')
        self.assertEqual(salad.results_adapter_key, 'salad')
        self.assertEqual(salad.projection_adapter_key, 'salad_state')
        self.assertEqual(salad.recheck_adapter_key, 'word_salad')
        self.assertEqual(salad.schedule.game_id, salad.game_id)
        self.assertTrue(salad.capabilities.statistics)

    def test_unknown_game_is_not_silently_treated_as_daily(self):
        self.assertIsNone(get_daily_game('new_game_not_registered'))

    def test_require_reports_unknown_game(self):
        with self.assertRaisesRegex(KeyError, 'unknown daily game'):
            DAILY_GAME_REGISTRY.require('new_game_not_registered')

    def test_results_adapter_exposes_both_daily_results_variants(self):
        alphabetty = get_daily_results_adapter('alphabetty')
        salad = get_daily_results_adapter('salad')

        self.assertEqual(alphabetty.aggregate_variant, 'alphabetty')
        self.assertEqual(alphabetty.task_variant, 'alphabetty')
        self.assertEqual(salad.aggregate_variant, 'standard')
        self.assertEqual(salad.task_variant, 'salad_words')

    def test_declared_adapter_keys_resolve_for_every_registered_game(self):
        for definition in DAILY_GAME_REGISTRY.all():
            if definition.results_adapter_key:
                adapter = get_daily_results_adapter(definition.game_id)
                self.assertIsNotNone(adapter, definition.game_id)
                self.assertEqual(adapter.key, definition.results_adapter_key)
            if definition.board_adapter_key:
                self.assertIn(definition.board_adapter_key, DAILY_BOARD_ADAPTERS)
            if definition.share_adapter_key:
                self.assertIn(definition.share_adapter_key, DAILY_SHARE_ADAPTERS)

    def test_registry_rejects_duplicate_ids(self):
        definition = DAILY_GAME_REGISTRY.require('ladder')

        with self.assertRaises(ValueError):
            DailyGameRegistry((definition, definition))

    def test_registry_rejects_schedule_identity_mismatch(self):
        definition = DAILY_GAME_REGISTRY.require('ladder')

        with self.assertRaisesRegex(ValueError, 'must match its schedule'):
            DailyGameRegistry((replace(definition, game_id='other'),))
