from datetime import date

from django.test import SimpleTestCase

from games.ads_direct.client import redact
from games.ads_direct.config import (
    ConfigError,
    assert_budget_cap,
    assert_minus_phrases_safe,
    campaign_add_item,
    custom_period_budget,
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
        budget = custom_period_budget(
            spend_limit_rub=500,
            start=date(2026, 9, 8),
            end=date(2026, 9, 18),
        )
        self.assertEqual(budget["SpendLimit"], 500_000_000)
        self.assertEqual(budget["AutoContinue"], "NO")
        campaign = campaign_add_item()
        network = campaign["UnifiedCampaign"]["BiddingStrategy"]["Network"]
        self.assertEqual(network["BiddingStrategyType"], "WB_MAXIMUM_CLICKS")
        self.assertNotIn("WeeklySpendLimit", network["WbMaximumClicks"])
        self.assertEqual(campaign["UnifiedCampaign"]["BiddingStrategy"]["Search"]["BiddingStrategyType"], "SERVING_OFF")


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
