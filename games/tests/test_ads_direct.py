from datetime import date

from unittest.mock import Mock

from django.test import SimpleTestCase

from games.ads_direct.client import redact
from games.ads_direct import ops
from games.ads_direct.config import (
    ConfigError,
    assert_budget_cap,
    assert_minus_phrases_safe,
    campaign_add_item,
    custom_period_budget,
    mobile_off_bidmodifier,
    tablet_off_bidmodifier,
)
from games.ads_direct.constants import CAMPAIGN_NEGATIVE_KEYWORDS, MAX_EXPERIMENT_SPEND_RUB
from games.ads_direct.money import micros_to_rubles, rubles_to_micros


class MoneyTests(SimpleTestCase):
    def test_500_rubles_is_500_million_micros(self):
        self.assertEqual(rubles_to_micros(500), 500_000_000)
        self.assertEqual(micros_to_rubles(500_000_000), 500)


class BudgetGuardrailTests(SimpleTestCase):
    def test_rejects_spend_above_experiment_cap(self):
        with self.assertRaises(ConfigError):
            assert_budget_cap(MAX_EXPERIMENT_SPEND_RUB + 1)

    def test_custom_period_has_no_weekly_limit_and_no_autocontinue(self):
        with self.assertRaisesRegex(ConfigError, "at least 550"):
            custom_period_budget(
                spend_limit_rub=500,
                start=date(2026, 9, 8),
                end=date(2026, 9, 18),
            )

        budget = custom_period_budget(
            spend_limit_rub=500,
            start=date(2026, 9, 8),
            end=date(2026, 9, 9),
        )
        self.assertEqual(budget["SpendLimit"], 500_000_000)
        self.assertEqual(budget["AutoContinue"], "NO")
        campaign = campaign_add_item(start=date(2026, 9, 8), end=date(2026, 9, 9))
        network = campaign["UnifiedCampaign"]["BiddingStrategy"]["Network"]
        self.assertEqual(network["BiddingStrategyType"], "WB_MAXIMUM_CLICKS")
        self.assertNotIn("WeeklySpendLimit", network["WbMaximumClicks"])
        self.assertEqual(campaign["UnifiedCampaign"]["BiddingStrategy"]["Search"]["BiddingStrategyType"], "SERVING_OFF")

    def test_one_day_period_can_use_the_experiment_cap(self):
        budget = custom_period_budget(
            spend_limit_rub=500,
            start=date(2026, 9, 8),
            end=date(2026, 9, 9),
        )
        self.assertEqual(budget["SpendLimit"], 500_000_000)

    def test_device_exclusions_are_serialized_separately(self):
        self.assertEqual(
            mobile_off_bidmodifier(campaign_id=7),
            {"CampaignId": 7, "MobileAdjustment": {"BidModifier": 0}},
        )
        self.assertEqual(
            tablet_off_bidmodifier(campaign_id=7),
            {"CampaignId": 7, "TabletAdjustment": {"BidModifier": 0}},
        )

    def test_priority_goals_start_with_game_start(self):
        campaign = campaign_add_item(start=date(2026, 9, 8), end=date(2026, 9, 9))
        goals = campaign["UnifiedCampaign"]["PriorityGoals"]["Items"]
        self.assertEqual(goals[0]["GoalId"], 595331160)
        self.assertEqual(goals[1]["GoalId"], 595331071)

    def test_direct_rejection_cannot_change_requested_window_or_budget(self):
        client = Mock()
        client.call.side_effect = ops.DirectApiError("budget rejected")
        with self.assertRaises(ConfigError):
            ops._add_campaign_resilient(
                client,
                start=date(2026, 9, 8),
                end=date(2026, 9, 9),
            )
        self.assertTrue(client.call.call_args_list)
        for call in client.call.call_args_list:
            payload = call.args[2]["Campaigns"][0]
            budget = payload["UnifiedCampaign"]["BiddingStrategy"]["Network"]["WbMaximumClicks"]["CustomPeriodBudget"]
            self.assertEqual(payload["StartDate"], "2026-09-08")
            self.assertEqual(budget["StartDate"], "2026-09-08")
            self.assertEqual(budget["EndDate"], "2026-09-09")
            self.assertEqual(budget["SpendLimit"], 500_000_000)


class MinusPhraseTests(SimpleTestCase):
    def test_configured_minuses_are_safe(self):
        assert_minus_phrases_safe(CAMPAIGN_NEGATIVE_KEYWORDS)

    def test_rejects_minus_that_blocks_wordle(self):
        with self.assertRaises(ConfigError):
            assert_minus_phrases_safe(["вордли"])


class RedactTests(SimpleTestCase):
    def test_redacts_bearer_token(self):
        token = "secret-token-value"
        text = redact(f'Authorization: Bearer {token} extra', token)
        self.assertNotIn(token, text)
        self.assertIn("[REDACTED]", text)
