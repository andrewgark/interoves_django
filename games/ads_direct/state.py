"""Machine-readable advertising state. No secrets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from games.ads_direct.constants import (
    BASELINE,
    MAX_EXPERIMENT_SPEND_RUB,
    METRIKA_COUNTER_ID,
    OLD_MASTER_CAMPAIGN_ID,
    PRODUCT_GOALS,
    RESERVE_BUDGET_RUB,
)

STATE_RELATIVE = Path("ads") / "state.json"


def state_path() -> Path:
    return Path(settings.BASE_DIR) / STATE_RELATIVE


def default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "old_master_campaign_id": OLD_MASTER_CAMPAIGN_ID,
        "old_master_notes": (
            "Master-campaign campaign. Visible in Reports/Metrika, not in Campaigns/AdGroups/"
            "Ads/Keywords API. Historical baseline only. Never edit or resume via API."
        ),
        "current_managed_campaign_id": None,
        "current_managed_campaign_name": None,
        "campaign_creation_date": None,
        "metrika_counter_id": METRIKA_COUNTER_ID,
        "product_goal_ids": PRODUCT_GOALS,
        "allowed_test_budget_rub": MAX_EXPERIMENT_SPEND_RUB,
        "reserve_budget_rub": RESERVE_BUDGET_RUB,
        "max_experiment_spend_rub": MAX_EXPERIMENT_SPEND_RUB,
        "ids": {
            "adgroup_ids": [],
            "ad_ids": [],
            "keyword_ids": [],
            "autotargeting_ids": [],
            "bidmodifier_ids": [],
        },
        "baseline": BASELINE,
        "decisions": [],
        "safety": {
            "never_edit_old_master": True,
            "never_spend_reserve_without_permission": True,
            "never_enable_smartphones_without_permission": True,
            "never_create_second_paid_campaign_without_permission": True,
        },
    }


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return default_state()
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    merged = default_state()
    merged.update(data)
    return merged


def save_state(state: dict[str, Any]) -> Path:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)
    return path


def append_decision(state: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    item = dict(decision)
    item.setdefault("at", datetime.now(timezone.utc).isoformat())
    state.setdefault("decisions", []).append(item)
    return state
