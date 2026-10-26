"""Campaign/ad payloads built from official Direct v501 schemas."""

from __future__ import annotations

from datetime import date, timedelta

from games.ads_direct.constants import (
    AD_DISPLAY_PATH,
    AD_TEXT,
    AD_TITLE,
    AD_TITLE2,
    CAMPAIGN_NAME,
    CAMPAIGN_NEGATIVE_KEYWORDS,
    CONTEXT_KEYWORDS,
    EXPERIMENT_END,
    EXPERIMENT_START,
    GOAL_GAME_COMPLETE,
    GOAL_GAME_START,
    LANDING_URL,
    MAX_EXPERIMENT_SPEND_RUB,
    MIN_CUSTOM_PERIOD_BUDGET_RUB,
    METRIKA_COUNTER_ID,
    REGION_RUSSIA,
    SAFE_KEYWORD_STEMS,
    TIMEZONE,
)
from games.ads_direct.money import rubles_to_micros


class ConfigError(ValueError):
    pass


def assert_budget_cap(spend_limit_rub: float) -> None:
    if spend_limit_rub > MAX_EXPERIMENT_SPEND_RUB:
        raise ConfigError(
            f"SpendLimit {spend_limit_rub} ₽ exceeds the experiment cap "
            f"{MAX_EXPERIMENT_SPEND_RUB} ₽. Do not spend the reserve."
        )


def assert_minus_phrases_safe(phrases: list[str]) -> None:
    lowered = [p.casefold() for p in phrases]
    for stem in SAFE_KEYWORD_STEMS:
        for phrase in lowered:
            if stem in phrase:
                raise ConfigError(f"Minus-phrase {phrase!r} would block relevant stem {stem!r}")


def custom_period_budget(*, spend_limit_rub: float, start: date, end: date) -> dict:
    assert_budget_cap(spend_limit_rub)
    if end <= start:
        raise ConfigError("CustomPeriodBudget EndDate must be after StartDate")
    if end - start > timedelta(days=1) and spend_limit_rub < MIN_CUSTOM_PERIOD_BUDGET_RUB:
        raise ConfigError(
            "Direct requires at least {} ₽ for a custom period longer than one day; "
            "refusing to shorten the campaign implicitly. Choose a shorter period or "
            "explicitly raise the approved budget cap.".format(MIN_CUSTOM_PERIOD_BUDGET_RUB)
        )
    return {
        "SpendLimit": rubles_to_micros(spend_limit_rub),
        "StartDate": start.isoformat(),
        "EndDate": end.isoformat(),
        "AutoContinue": "NO",
    }


def campaign_add_item(
    *,
    name: str = CAMPAIGN_NAME,
    spend_limit_rub: float = MAX_EXPERIMENT_SPEND_RUB,
    start: date = EXPERIMENT_START,
    end: date = EXPERIMENT_END,
    negative_keywords: list[str] | None = None,
) -> dict:
    """UNIFIED_CAMPAIGN: РСЯ WB_MAXIMUM_CLICKS, Search SERVING_OFF, CustomPeriodBudget.

    WeeklySpendLimit is omitted on purpose: Direct forbids combining it with
    CustomPeriodBudget on create.
    Conversion strategies are not used: 500 ₽ and ~0 ticket_purchase are not
    enough to train CPA. Quality is measured in Metrika.
    """
    negatives = list(negative_keywords if negative_keywords is not None else CAMPAIGN_NEGATIVE_KEYWORDS)
    assert_minus_phrases_safe(negatives)
    budget = custom_period_budget(spend_limit_rub=spend_limit_rub, start=start, end=end)
    return {
        "Name": name,
        "StartDate": start.isoformat(),
        "TimeZone": TIMEZONE,
        "NegativeKeywords": {"Items": negatives},
        "UnifiedCampaign": {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": "SERVING_OFF"},
                "Network": {
                    "BiddingStrategyType": "WB_MAXIMUM_CLICKS",
                    "WbMaximumClicks": {"CustomPeriodBudget": budget},
                    "PlacementTypes": {"Network": "YES", "Maps": "NO"},
                },
            },
            "Settings": [
                {"Option": "ADD_METRICA_TAG", "Value": "YES"},
                {"Option": "ENABLE_SITE_MONITORING", "Value": "YES"},
                {"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "NO"},
                {"Option": "ALTERNATIVE_TEXTS_ENABLED", "Value": "NO"},
            ],
            "CounterIds": {"Items": [METRIKA_COUNTER_ID]},
            "PriorityGoals": {
                "Items": [
                    {"GoalId": GOAL_GAME_START, "Value": rubles_to_micros(1)},
                    {"GoalId": GOAL_GAME_COMPLETE, "Value": rubles_to_micros(75)},
                ]
            },
            "TrackingParams": "utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_id}&utm_content={ad_id}",
            "AttributionModel": "LC",
        },
    }


def adgroup_add_item(*, campaign_id: int, name: str) -> dict:
    return {
        "Name": name,
        "CampaignId": campaign_id,
        "RegionIds": [REGION_RUSSIA],
        "UnifiedAdGroup": {"OfferRetargeting": "NO"},
    }


def ad_add_item(*, adgroup_id: int) -> dict:
    href = (
        f"{LANDING_URL}?utm_source=yandex&utm_medium=cpc"
        "&utm_campaign={campaign_id}&utm_content={ad_id}"
    )
    return {
        "AdGroupId": adgroup_id,
        "TextAd": {
            "Title": AD_TITLE,
            "Title2": AD_TITLE2,
            "Text": AD_TEXT,
            "Href": href,
            "Mobile": "NO",
            "DisplayUrlPath": AD_DISPLAY_PATH,
        },
    }


def keyword_add_items(*, adgroup_id: int, keywords: list[str] | None = None) -> list[dict]:
    phrases = keywords if keywords is not None else CONTEXT_KEYWORDS
    return [{"Keyword": phrase, "AdGroupId": adgroup_id} for phrase in phrases]


def mobile_off_bidmodifier(*, campaign_id: int) -> dict:
    """Coefficient 0 = do not show on smartphones. Official range is 0–1300."""
    return {
        "CampaignId": campaign_id,
        "MobileAdjustment": {"BidModifier": 0},
    }


def tablet_off_bidmodifier(*, campaign_id: int) -> dict:
    """Coefficient 0 = do not show on tablets."""
    return {
        "CampaignId": campaign_id,
        "TabletAdjustment": {"BidModifier": 0},
    }


def extract_add_id(result: dict, *, index: int = 0) -> int:
    rows = result.get("AddResults") or []
    if index >= len(rows):
        raise ConfigError(f"AddResults missing index {index}: {result}")
    row = rows[index]
    if row.get("Errors"):
        raise ConfigError(f"Direct add error: {row['Errors']}")
    if "Id" in row:
        return int(row["Id"])
    ids = row.get("Ids") or []
    if ids:
        return int(ids[0])
    raise ConfigError(f"No Id in AddResults: {row}")


def extract_add_ids(result: dict) -> list[int]:
    out = []
    for row in result.get("AddResults") or []:
        if row.get("Errors"):
            raise ConfigError(f"Direct add error: {row['Errors']}")
        if "Id" in row:
            out.append(int(row["Id"]))
        else:
            out.extend(int(x) for x in (row.get("Ids") or []))
    return out
