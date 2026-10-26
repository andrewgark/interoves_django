"""High-level Direct/Metrika operations used by the ads CLI."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Any

from games.ads_direct import fields
from games.ads_direct.audit import append_audit
from games.ads_direct.client import AdsClient, DirectApiError, default_client
from games.ads_direct.config import (
    ConfigError,
    ad_add_item,
    adgroup_add_item,
    campaign_add_item,
    extract_add_id,
    extract_add_ids,
    keyword_add_items,
    mobile_off_bidmodifier,
    tablet_off_bidmodifier,
)
from games.ads_direct.constants import (
    AD_GROUP_NAME,
    AD_TITLE,
    CAMPAIGN_NAME,
    CONTEXT_KEYWORDS,
    GOAL_ACTIVATED,
    GOAL_GAME_COMPLETE,
    GOAL_GAME_START,
    GOAL_NAME_ALIASES,
    GOAL_TICKET_PURCHASE,
    LANDING_URL,
    MAX_EXPERIMENT_SPEND_RUB,
    METRIKA_COUNTER_ID,
    OLD_MASTER_CAMPAIGN_ID,
    PRODUCT_GOALS,
    REGION_RUSSIA,
)
from games.ads_direct.money import micros_to_rubles, parse_report_money
from games.ads_direct.state import append_decision, load_state, save_state


def client_info(client: AdsClient | None = None) -> dict:
    ads = client or default_client()
    login = ads.login_info()
    clients = ads.call(
        "clients",
        "get",
        {
            "FieldNames": [
                "AccountQuality",
                "Archived",
                "ClientId",
                "ClientInfo",
                "Currency",
                "Grants",
                "Login",
                "Restrictions",
                "Type",
                "VatRate",
                "AvailableCampaignTypes",
            ]
        },
    )
    return {"oauth": login, "clients": clients, "units": ads.last_units}


def dictionaries(client: AdsClient | None = None, names: list[str] | None = None) -> dict:
    ads = client or default_client()
    return ads.call(
        "dictionaries",
        "get",
        {"DictionaryNames": names or ["Currencies", "Constants", "TimeZones"]},
    )


def campaigns_get(client: AdsClient, ids: list[int] | None = None) -> list[dict]:
    criteria: dict[str, Any] = {}
    if ids:
        criteria["Ids"] = ids
    result = client.call(
        "campaigns",
        "get",
        {
            "SelectionCriteria": criteria,
            "FieldNames": fields.CAMPAIGN_FIELDS,
            "UnifiedCampaignFieldNames": fields.UNIFIED_CAMPAIGN_FIELDS,
        },
    )
    return result.get("Campaigns") or []


def adgroups_get(client: AdsClient, *, campaign_ids: list[int] | None = None, ids: list[int] | None = None) -> list[dict]:
    criteria: dict[str, Any] = {}
    if campaign_ids:
        criteria["CampaignIds"] = campaign_ids
    if ids:
        criteria["Ids"] = ids
    result = client.call(
        "adgroups",
        "get",
        {
            "SelectionCriteria": criteria,
            "FieldNames": fields.ADGROUP_FIELDS,
            "UnifiedAdGroupFieldNames": fields.UNIFIED_ADGROUP_FIELDS,
        },
    )
    return result.get("AdGroups") or []


def ads_get(client: AdsClient, *, campaign_ids: list[int] | None = None, ids: list[int] | None = None) -> list[dict]:
    criteria: dict[str, Any] = {}
    if campaign_ids:
        criteria["CampaignIds"] = campaign_ids
    if ids:
        criteria["Ids"] = ids
    result = client.call(
        "ads",
        "get",
        {
            "SelectionCriteria": criteria,
            "FieldNames": fields.AD_FIELDS,
            "TextAdFieldNames": fields.TEXT_AD_FIELDS,
        },
    )
    return result.get("Ads") or []


def keywords_get(client: AdsClient, *, campaign_ids: list[int] | None = None, ids: list[int] | None = None) -> list[dict]:
    criteria: dict[str, Any] = {}
    if campaign_ids:
        criteria["CampaignIds"] = campaign_ids
    if ids:
        criteria["Ids"] = ids
    params: dict[str, Any] = {
        "SelectionCriteria": criteria,
        "FieldNames": fields.KEYWORD_FIELDS,
    }
    try:
        result = client.call(
            "keywords",
            "get",
            {**params, "AutotargetingSettingsFieldNames": fields.KEYWORD_AUTOTARGETING_FIELDS},
        )
    except DirectApiError:
        result = client.call("keywords", "get", params)
    return result.get("Keywords") or []


def bidmodifiers_get(client: AdsClient, *, campaign_ids: list[int] | None = None, ids: list[int] | None = None) -> list[dict]:
    criteria: dict[str, Any] = {"Levels": ["CAMPAIGN", "AD_GROUP"]}
    if campaign_ids:
        criteria["CampaignIds"] = campaign_ids
    if ids:
        criteria["Ids"] = ids
    result = client.call(
        "bidmodifiers",
        "get",
        {
            "SelectionCriteria": criteria,
            "FieldNames": fields.BIDMODIFIER_FIELDS,
            "MobileAdjustmentFieldNames": fields.BIDMODIFIER_MOBILE_FIELDS,
            "TabletAdjustmentFieldNames": fields.BIDMODIFIER_TABLET_FIELDS,
        },
    )
    return result.get("BidModifiers") or []


def metrika_goals(client: AdsClient | None = None, counter_id: int = METRIKA_COUNTER_ID) -> list[dict]:
    ads = client or default_client()
    data = ads.metrika_get(f"/management/v1/counter/{counter_id}/goals")
    return data.get("goals") or []


def campaign_report(
    client: AdsClient,
    campaign_id: int,
    *,
    date_from: str,
    date_to: str,
    extra_fields: list[str] | None = None,
    extra_filters: list[dict] | None = None,
) -> list[dict]:
    field_names = ["CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Ctr", "AvgCpc"]
    if extra_fields:
        field_names.extend(extra_fields)
    selection: dict[str, Any] = {
        "DateFrom": date_from,
        "DateTo": date_to,
        "Filter": [{"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}],
    }
    if extra_filters:
        selection["Filter"].extend(extra_filters)
    return client.reports(
        {
            "SelectionCriteria": selection,
            "FieldNames": field_names,
            "ReportName": f"interoves campaign {campaign_id} {date_from} {date_to}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }
    )


def sliced_report(
    client: AdsClient,
    campaign_id: int,
    *,
    date_from: str,
    date_to: str,
    field_names: list[str],
    report_type: str = "CUSTOM_REPORT",
) -> list[dict]:
    return client.reports(
        {
            "SelectionCriteria": {
                "DateFrom": date_from,
                "DateTo": date_to,
                "Filter": [{"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}],
            },
            "FieldNames": field_names,
            "ReportName": f"interoves slice {campaign_id} {' '.join(field_names[:4])}",
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }
    )


def search_query_report(client: AdsClient, campaign_id: int, *, date_from: str, date_to: str) -> list[dict]:
    return client.reports(
        {
            "SelectionCriteria": {
                "DateFrom": date_from,
                "DateTo": date_to,
                "Filter": [{"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}],
            },
            "FieldNames": ["Query", "Impressions", "Clicks", "Cost", "Ctr", "AvgCpc"],
            "ReportName": f"interoves queries {campaign_id} {date_from}",
            "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }
    )


def metrika_campaign_quality(client: AdsClient, campaign_id: int, *, date_from: str, date_to: str) -> dict:
    metrics = [
        "ym:s:users",
        "ym:s:newUsers",
        "ym:s:visits",
        f"ym:s:goal{GOAL_GAME_START}users",
        f"ym:s:goal{GOAL_GAME_COMPLETE}users",
        f"ym:s:goal{GOAL_ACTIVATED}users",
        f"ym:s:goal{GOAL_TICKET_PURCHASE}users",
    ]
    data = client.metrika_get(
        "/stat/v1/data",
        {
            "ids": METRIKA_COUNTER_ID,
            "date1": date_from,
            "date2": date_to,
            "metrics": ",".join(metrics),
            "filters": f"ym:s:lastDirectClickOrder=='{campaign_id}'",
            "accuracy": "full",
        },
    )
    totals = (data.get("totals") or [0] * len(metrics))
    return {
        "users": _num(totals, 0),
        "new_users": _num(totals, 1),
        "visits": _num(totals, 2),
        "game_start": _num(totals, 3),
        "game_complete": _num(totals, 4),
        "activated": _num(totals, 5),
        "ticket_purchase": _num(totals, 6),
    }


def metrika_breakdown(client: AdsClient, campaign_id: int, *, date_from: str, date_to: str, dimension: str) -> list[dict]:
    metrics = [
        "ym:s:users",
        f"ym:s:goal{GOAL_GAME_START}users",
        f"ym:s:goal{GOAL_GAME_COMPLETE}users",
        f"ym:s:goal{GOAL_ACTIVATED}users",
    ]
    data = client.metrika_get(
        "/stat/v1/data",
        {
            "ids": METRIKA_COUNTER_ID,
            "date1": date_from,
            "date2": date_to,
            "metrics": ",".join(metrics),
            "dimensions": dimension,
            "filters": f"ym:s:lastDirectClickOrder=='{campaign_id}'",
            "accuracy": "full",
            "limit": 20,
        },
    )
    rows = []
    for row in data.get("data") or []:
        dims = row.get("dimensions") or [{}]
        mets = row.get("metrics") or []
        rows.append(
            {
                "name": (dims[0] or {}).get("name") or (dims[0] or {}).get("id"),
                "users": _num(mets, 0),
                "game_start": _num(mets, 1),
                "game_complete": _num(mets, 2),
                "activated": _num(mets, 3),
            }
        )
    return rows


def currency_limits(dicts: dict) -> dict:
    out = {}
    for item in dicts.get("Currencies") or []:
        if item.get("Currency") != "RUB":
            continue
        props = {p["Name"]: p.get("Value") for p in item.get("Properties") or []}
        out = {
            "currency": "RUB",
            "minimum_weekly_spend_limit_rub": micros_to_rubles(props.get("MinimumWeeklySpendLimit") or 0),
            "minimum_daily_budget_rub": micros_to_rubles(props.get("MinimumDailyBudget") or 0),
            "minimum_bid_rub": micros_to_rubles(props.get("MinimumBid") or props.get("MinBid") or 0),
            "maximum_bid_rub": micros_to_rubles(props.get("MaximumBid") or props.get("MaxBid") or 0),
            "properties": props,
        }
    return out


def old_campaign_spend_check(client: AdsClient, *, today: date | None = None, wait_seconds: int = 45) -> dict:
    today = today or date.today()
    date_from = (today - timedelta(days=2)).isoformat()
    date_to = today.isoformat()
    first = campaign_report(client, OLD_MASTER_CAMPAIGN_ID, date_from=date_from, date_to=date_to)
    daily = sliced_report(
        client,
        OLD_MASTER_CAMPAIGN_ID,
        date_from=date_from,
        date_to=date_to,
        field_names=["Date", "CampaignId", "Impressions", "Clicks", "Cost"],
    )
    today_cost = 0.0
    today_impr = 0.0
    for row in daily:
        if str(row.get("Date") or "") == today.isoformat() or str(row.get("Date") or "").replace("-", "") == today.strftime("%Y%m%d"):
            today_cost = parse_report_money(row.get("Cost"))
            today_impr = parse_report_money(row.get("Impressions"))
    still_spending = False
    second_cost = today_cost
    second_impr = today_impr
    if today_cost > 0:
        time.sleep(wait_seconds)
        daily2 = sliced_report(
            client,
            OLD_MASTER_CAMPAIGN_ID,
            date_from=today.isoformat(),
            date_to=today.isoformat(),
            field_names=["Date", "CampaignId", "Impressions", "Clicks", "Cost"],
        )
        second_cost = _sum_cost(daily2)
        second_impr = _sum_field(daily2, "Impressions")
        # Delayed billing can bump Cost slightly after a stop. New impressions mean it is still serving.
        if second_cost - today_cost > 1 and second_impr > today_impr:
            still_spending = True
    return {
        "campaign_id": OLD_MASTER_CAMPAIGN_ID,
        "daily": daily,
        "today_cost_rub": today_cost,
        "today_cost_rub_after_wait": second_cost,
        "today_impressions": today_impr,
        "today_impressions_after_wait": second_impr,
        "still_spending": still_spending,
        "first_window_cost_rub": _sum_cost(first),
    }


def preflight(client: AdsClient | None = None, *, spend_wait_seconds: int = 45) -> dict:
    ads = client or default_client()
    info = client_info(ads)
    dicts = dictionaries(ads)
    limits = currency_limits(dicts)
    goals = metrika_goals(ads)
    goal_check = _match_product_goals(goals)
    regions = ads.call("dictionaries", "get", {"DictionaryNames": ["GeoRegions"]})
    russia = next((r for r in (regions.get("GeoRegions") or []) if r.get("GeoRegionId") == REGION_RUSSIA), None)
    spend = old_campaign_spend_check(ads, wait_seconds=spend_wait_seconds)
    min_weekly = limits.get("minimum_weekly_spend_limit_rub") or 0
    budget_ok = MAX_EXPERIMENT_SPEND_RUB >= min_weekly or min_weekly == 0
    issues = []
    if not goal_check["ok"]:
        issues.append(f"Metrika goal mismatch: {goal_check}")
    if not budget_ok:
        issues.append(
            f"MinimumWeeklySpendLimit is {min_weekly} ₽, experiment cap is {MAX_EXPERIMENT_SPEND_RUB} ₽"
        )
    if russia is None:
        issues.append("Region 225 (Russia) not found in GeoRegions")
    clients = (info.get("clients") or {}).get("Clients") or []
    if not clients:
        issues.append("Clients.get returned no clients")
    else:
        ctypes = clients[0].get("AvailableCampaignTypes") or []
        if "UNIFIED" not in ctypes and "UNIFIED_CAMPAIGN" not in ctypes:
            issues.append(f"UNIFIED campaign type not in AvailableCampaignTypes: {ctypes}")
    return {
        "oauth": info["oauth"],
        "client": _public_client(clients[0] if clients else {}),
        "currency_limits": limits,
        "budget_ok_for_500": budget_ok,
        "region_225": russia,
        "metrika_goals": goal_check,
        "old_master_spend": spend,
        "issues": issues,
        "ok": not issues and not spend["still_spending"],
        "can_launch": not issues and not spend["still_spending"] and budget_ok,
    }


def launch(
    client: AdsClient | None = None,
    *,
    dry_run: bool = False,
    moderate: bool = True,
    spend_wait_seconds: int = 45,
) -> dict:
    ads = client or default_client()
    state = load_state()
    if state.get("current_managed_campaign_id") and not dry_run:
        raise ConfigError(
            f"Managed campaign {state['current_managed_campaign_id']} already exists. "
            "Refusing to create a second paid campaign."
        )
    pf = preflight(ads, spend_wait_seconds=spend_wait_seconds)
    if dry_run:
        return {
            "dry_run": True,
            "write_requests": 0,
            "preflight": pf,
            "payloads": _dry_run_payloads(),
        }
    if pf["old_master_spend"]["still_spending"]:
        append_audit(
            action="abort_launch",
            object_type="campaign",
            object_id=OLD_MASTER_CAMPAIGN_ID,
            reason="Old Master campaign is still spending",
            metrics=pf["old_master_spend"],
            api_status="blocked",
        )
        raise ConfigError("Old Master campaign 713889884 is still spending. New campaign not launched.")
    if not pf["budget_ok_for_500"]:
        min_weekly = pf["currency_limits"].get("minimum_weekly_spend_limit_rub")
        raise ConfigError(
            f"Minimum allowed weekly budget is {min_weekly} ₽, which is above the 500 ₽ experiment cap. "
            "Campaign not created. Do not use the 900 ₽ reserve."
        )
    if pf["issues"]:
        raise ConfigError("Preflight failed: " + "; ".join(pf["issues"]))

    created = _create_tree(ads)
    safety = safety_check(ads, created["campaign_id"], created)
    created["safety"] = safety
    launched = False
    if safety["ok"] and moderate:
        _moderate_and_resume(ads, created)
        launched = True
    elif safety["ok"] and not moderate:
        ads.call("campaigns", "suspend", {"SelectionCriteria": {"Ids": [created["campaign_id"]]}})
    else:
        ads.call("campaigns", "suspend", {"SelectionCriteria": {"Ids": [created["campaign_id"]]}})
        append_audit(
            action="suspend",
            object_type="campaign",
            object_id=created["campaign_id"],
            reason="Safety check failed; campaign left suspended",
            metrics={"checks": safety["failures"]},
            api_status="suspended",
        )
    state = load_state()
    state["current_managed_campaign_id"] = created["campaign_id"]
    state["current_managed_campaign_name"] = CAMPAIGN_NAME
    state["campaign_creation_date"] = date.today().isoformat()
    state["ids"] = {
        "adgroup_ids": created["adgroup_ids"],
        "ad_ids": created["ad_ids"],
        "keyword_ids": created["keyword_ids"],
        "autotargeting_ids": created["autotargeting_ids"],
        "bidmodifier_ids": created["bidmodifier_ids"],
    }
    append_decision(
        state,
        {
            "type": "launch",
            "campaign_id": created["campaign_id"],
            "launched": launched,
            "reason": created.get("reason"),
            "safety_ok": safety["ok"],
        },
    )
    save_state(state)
    created["launched"] = launched
    created["preflight"] = {
        "old_master_still_spending": pf["old_master_spend"]["still_spending"],
        "today_cost_rub": pf["old_master_spend"]["today_cost_rub"],
        "oauth_login": pf["oauth"].get("login"),
    }
    return created


def _dry_run_payloads() -> dict:
    start = date.today()
    end = start + timedelta(days=10)
    try:
        campaign = campaign_add_item(start=start, end=end)
    except ConfigError as exc:
        return {
            "campaign_spec": {
                "budget_rub": MAX_EXPERIMENT_SPEND_RUB,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "duration_days": (end - start).days,
            },
            "validation_error": str(exc),
            "write_requests": 0,
            "notes": ["No Direct payload generated because validation failed closed."],
        }
    return {
        "campaigns.add": {"Campaigns": [campaign]},
        "adgroups.add": {"AdGroups": [adgroup_add_item(campaign_id=0, name=AD_GROUP_NAME)]},
        "ads.add": {"Ads": [ad_add_item(adgroup_id=0)]},
        "keywords.add": {"Keywords": keyword_add_items(adgroup_id=0)},
        "bidmodifiers.add": {
            "BidModifiers": [
                mobile_off_bidmodifier(campaign_id=0),
                tablet_off_bidmodifier(campaign_id=0),
            ]
        },
        "notes": [
            "Search SERVING_OFF, Network WB_MAXIMUM_CLICKS + CustomPeriodBudget 500 ₽ AutoContinue=NO",
            "No WeeklySpendLimit (forbidden together with CustomPeriodBudget)",
            "No autotargeting: Master campaign AT produced IQ/quiz/crossword junk",
            "Smartphones and tablets BidModifier=0",
            "Region 225 only",
            "Strategy is max clicks because 500 ₽ cannot train CPA; quality via Metrika complete/activated",
        ],
    }


def _add_campaign_resilient(ads: AdsClient, *, start: date, end: date) -> tuple[dict, dict]:
    """Create UNIFIED campaign; retry only with safer same-cap variants, never a higher budget."""
    # Keep the requested window invariant. A previous fallback silently changed
    # a ten-day experiment into a one-day campaign after Direct rejected the
    # budget, which made the campaign stop before quality could be evaluated.
    attempts = [
        campaign_add_item(start=start, end=end),
        campaign_add_item(name=f"{CAMPAIGN_NAME} — {start.isoformat()}", start=start, end=end),
    ]
    last_error: Exception | None = None
    for item in attempts:
        for candidate in (item, _without_priority_goals(item)):
            try:
                result = ads.call("campaigns", "add", {"Campaigns": [candidate]})
                extract_add_id(result)
                return result, candidate
            except (DirectApiError, ConfigError) as exc:
                last_error = exc
                append_audit(
                    action="create_retry",
                    object_type="campaign",
                    reason=str(exc),
                    api_status="error",
                )
    raise ConfigError(f"Campaigns.add failed after retries: {last_error}")


def _without_priority_goals(item: dict) -> dict:
    clone = json.loads(json.dumps(item))
    clone.get("UnifiedCampaign", {}).pop("PriorityGoals", None)
    return clone


def _create_tree(ads: AdsClient) -> dict:
    reason = (
        "РСЯ-only UNIFIED campaign: historical РСЯ CPA and game.yandex.ru quality were better "
        "than search; budget must not be split; autotargeting off; smartphones and tablets off."
    )
    today = date.today()
    end = today + timedelta(days=10)
    add_campaign, campaign_item = _add_campaign_resilient(ads, start=today, end=end)
    campaign_id = extract_add_id(add_campaign)
    append_audit(
        action="create",
        object_type="campaign",
        object_id=campaign_id,
        new_value={"name": campaign_item["Name"], "type": "UNIFIED_CAMPAIGN"},
        reason=reason,
        api_status="created",
    )
    state = load_state()
    state["current_managed_campaign_id"] = campaign_id
    state["current_managed_campaign_name"] = campaign_item["Name"]
    state["campaign_creation_date"] = today.isoformat()
    save_state(state)
    got = campaigns_get(ads, [campaign_id])
    if not got:
        raise ConfigError(f"Campaigns.get empty after add id={campaign_id}")
    # Keep it suspended until safety + moderate.
    ads.call("campaigns", "suspend", {"SelectionCriteria": {"Ids": [campaign_id]}})
    append_audit(
        action="suspend",
        object_type="campaign",
        object_id=campaign_id,
        reason="Suspend immediately after create, before moderation",
        api_status="suspended",
    )

    add_group = ads.call(
        "adgroups",
        "add",
        {"AdGroups": [adgroup_add_item(campaign_id=campaign_id, name=AD_GROUP_NAME)]},
    )
    adgroup_id = extract_add_id(add_group)
    append_audit(
        action="create",
        object_type="adgroup",
        object_id=adgroup_id,
        new_value={"name": AD_GROUP_NAME, "region": REGION_RUSSIA},
        reason="Single РСЯ group; do not split 500 ₽ across experiments",
        api_status="created",
    )
    groups = adgroups_get(ads, ids=[adgroup_id])
    if not groups:
        raise ConfigError("AdGroups.get empty after add")

    add_ad = ads.call("ads", "add", {"Ads": [ad_add_item(adgroup_id=adgroup_id)]})
    ad_id = extract_add_id(add_ad)
    append_audit(
        action="create",
        object_type="ad",
        object_id=ad_id,
        new_value={"title": AD_TITLE, "href": LANDING_URL},
        reason="Same product promise as the Master banner: not a quiz, real thinking games",
        api_status="created",
    )
    ads_got = ads_get(ads, ids=[ad_id])
    if not ads_got:
        raise ConfigError("Ads.get empty after add")

    add_kw = ads.call("keywords", "add", {"Keywords": keyword_add_items(adgroup_id=adgroup_id)})
    keyword_ids = extract_add_ids(add_kw)
    append_audit(
        action="create",
        object_type="keywords",
        object_id=keyword_ids,
        new_value=CONTEXT_KEYWORDS,
        reason="Contextual phrases for РСЯ; no ---autotargeting",
        api_status="created",
    )
    kws = keywords_get(ads, ids=keyword_ids)
    if len(kws) != len(keyword_ids):
        raise ConfigError(f"Keywords.get mismatch: {len(kws)} vs {len(keyword_ids)}")

    add_bm = None
    device_adjustments = [
        mobile_off_bidmodifier(campaign_id=campaign_id),
        tablet_off_bidmodifier(campaign_id=campaign_id),
    ]
    try:
        add_bm = ads.call("bidmodifiers", "add", {"BidModifiers": device_adjustments})
        bidmodifier_ids = extract_add_ids(add_bm)
    except (DirectApiError, ConfigError) as exc:
        append_audit(
            action="create_retry",
            object_type="bidmodifier",
            reason=str(exc),
            api_status="error",
        )
        add_bm = ads.call(
            "bidmodifiers",
            "add",
            {
                "BidModifiers": [
                    {"AdGroupId": adgroup_id, "MobileAdjustment": {"BidModifier": 0}},
                    {"AdGroupId": adgroup_id, "TabletAdjustment": {"BidModifier": 0}},
                ]
            },
        )
        bidmodifier_ids = extract_add_ids(add_bm)
    append_audit(
        action="create",
        object_type="bidmodifier",
        object_id=bidmodifier_ids,
        new_value={"MobileAdjustment": 0, "TabletAdjustment": 0},
        reason="Exclude smartphones and tablets until device quality is revalidated",
        api_status="created",
    )
    bms = bidmodifiers_get(ads, campaign_ids=[campaign_id])
    return {
        "campaign_id": campaign_id,
        "adgroup_ids": [adgroup_id],
        "ad_ids": [ad_id],
        "keyword_ids": keyword_ids,
        "autotargeting_ids": [],
        "bidmodifier_ids": bidmodifier_ids,
        "campaign_get": got[0],
        "adgroup_get": groups[0],
        "ad_get": ads_got[0],
        "keywords_get": kws,
        "bidmodifiers_get": bms,
        "reason": reason,
        "campaigns_add": add_campaign,
        "adgroups_add": add_group,
        "ads_add": add_ad,
        "keywords_add": add_kw,
        "bidmodifiers_add": add_bm,
    }


def safety_check(ads: AdsClient, campaign_id: int, created: dict | None = None) -> dict:
    camp = (campaigns_get(ads, [campaign_id]) or [None])[0]
    failures = []
    if not camp:
        return {"ok": False, "failures": ["campaign not found"], "campaign": None}
    if camp.get("Type") != "UNIFIED_CAMPAIGN":
        failures.append(f"Type is {camp.get('Type')}, expected UNIFIED_CAMPAIGN")
    unified = camp.get("UnifiedCampaign") or {}
    strategy = unified_strategy(unified)
    if strategy.get("search_type") != "SERVING_OFF":
        failures.append(f"Search strategy is {strategy.get('search_type')}, expected SERVING_OFF")
    if strategy.get("network_type") != "WB_MAXIMUM_CLICKS":
        failures.append(f"Network strategy is {strategy.get('network_type')}")
    budget = strategy.get("custom_period_budget") or {}
    spend = micros_to_rubles(budget.get("SpendLimit") or 0)
    if spend > MAX_EXPERIMENT_SPEND_RUB or spend <= 0:
        failures.append(f"SpendLimit {spend} ₽ is not within 0..{MAX_EXPERIMENT_SPEND_RUB}")
    if str(budget.get("AutoContinue") or "").upper() != "NO":
        failures.append(f"AutoContinue is {budget.get('AutoContinue')}, expected NO")
    counters = ((unified.get("CounterIds") or {}).get("Items") or [])
    if METRIKA_COUNTER_ID not in counters:
        failures.append(f"CounterIds {counters} missing {METRIKA_COUNTER_ID}")
    href_ok = False
    for ad in ads_get(ads, campaign_ids=[campaign_id]):
        href = ((ad.get("TextAd") or {}).get("Href") or "")
        if "interoves.com" in href and "/start/" in href:
            href_ok = True
        else:
            failures.append(f"Ad {ad.get('Id')} href is not interoves.com/start/: {href[:80]}")
    if not href_ok:
        failures.append("No ad points at interoves.com/start/")
    groups = adgroups_get(ads, campaign_ids=[campaign_id])
    for group in groups:
        if REGION_RUSSIA not in (group.get("RegionIds") or []):
            failures.append(f"AdGroup {group.get('Id')} regions {group.get('RegionIds')}")
    mobiles = [
        bm
        for bm in bidmodifiers_get(ads, campaign_ids=[campaign_id])
        if bm.get("Type") == "MOBILE_ADJUSTMENT" or bm.get("MobileAdjustment")
    ]
    mobile_zero = False
    for bm in mobiles:
        adj = bm.get("MobileAdjustment") or {}
        if adj.get("BidModifier") == 0:
            mobile_zero = True
    if not mobile_zero:
        failures.append("MobileAdjustment BidModifier is not 0")
    tablets = [
        bm
        for bm in bidmodifiers_get(ads, campaign_ids=[campaign_id])
        if bm.get("Type") == "TABLET_ADJUSTMENT" or bm.get("TabletAdjustment")
    ]
    tablet_zero = any(
        (bm.get("TabletAdjustment") or {}).get("BidModifier") == 0
        for bm in tablets
    )
    if not tablet_zero:
        failures.append("TabletAdjustment BidModifier is not 0")
    others = [
        c
        for c in campaigns_get(ads)
        if c.get("Id") != campaign_id and c.get("State") == "ON" and c.get("Status") not in ("DRAFT", "ENDED")
    ]
    # Master is invisible in Campaigns.get; still flag any other API-visible ON campaign.
    if others:
        failures.append(f"Other ON campaigns: {[c.get('Id') for c in others]}")
    return {
        "ok": not failures,
        "failures": failures,
        "campaign": camp,
        "strategy": strategy,
        "spend_limit_rub": spend,
        "auto_continue": budget.get("AutoContinue"),
        "mobile_zero": mobile_zero,
        "tablet_zero": tablet_zero,
        "href_ok": href_ok,
    }


def unified_strategy(unified: dict) -> dict:
    bidding = unified.get("BiddingStrategy") or {}
    search = bidding.get("Search") or {}
    network = bidding.get("Network") or {}
    wb = network.get("WbMaximumClicks") or {}
    return {
        "search_type": search.get("BiddingStrategyType"),
        "network_type": network.get("BiddingStrategyType"),
        "network_placements": network.get("PlacementTypes"),
        "custom_period_budget": wb.get("CustomPeriodBudget"),
        "weekly_spend_limit": wb.get("WeeklySpendLimit"),
    }


def _moderate_and_resume(ads: AdsClient, created: dict) -> None:
    ad_ids = created["ad_ids"]
    ads.call("ads", "moderate", {"SelectionCriteria": {"Ids": ad_ids}})
    append_audit(
        action="moderate",
        object_type="ads",
        object_id=created["ad_ids"],
        reason="Submit DRAFT ads to ordinary moderation",
        api_status="submitted",
    )
    ads.call("campaigns", "resume", {"SelectionCriteria": {"Ids": [created["campaign_id"]]}})
    append_audit(
        action="resume",
        object_type="campaign",
        object_id=created["campaign_id"],
        reason="Safety checklist passed; resume after moderate",
        api_status="resumed",
    )


def keywords_suspend(client: AdsClient | None = None, ids: list[int] | None = None, *, dry_run: bool = False, reason: str = "") -> dict:
    ads = client or default_client()
    payload = {"SelectionCriteria": {"Ids": ids or []}}
    if dry_run:
        return {"dry_run": True, "method": "keywords.suspend", "params": payload}
    result = ads.call("keywords", "suspend", payload)
    append_audit(action="suspend", object_type="keywords", object_id=ids, reason=reason or "keyword suspend", api_status="ok")
    return result


def keywords_resume(client: AdsClient | None = None, ids: list[int] | None = None, *, dry_run: bool = False, reason: str = "") -> dict:
    ads = client or default_client()
    payload = {"SelectionCriteria": {"Ids": ids or []}}
    if dry_run:
        return {"dry_run": True, "method": "keywords.resume", "params": payload}
    result = ads.call("keywords", "resume", payload)
    append_audit(action="resume", object_type="keywords", object_id=ids, reason=reason or "keyword resume", api_status="ok")
    return result


def keywords_update(client: AdsClient, keywords: list[dict], *, dry_run: bool = False, reason: str = "") -> dict:
    payload = {"Keywords": keywords}
    if dry_run:
        return {"dry_run": True, "method": "keywords.update", "params": payload}
    result = (client or default_client()).call("keywords", "update", payload)
    append_audit(action="update", object_type="keywords", object_id=[k.get("Id") for k in keywords], new_value=keywords, reason=reason, api_status="ok")
    return result


def adgroups_update(client: AdsClient, adgroups: list[dict], *, dry_run: bool = False, reason: str = "") -> dict:
    payload = {"AdGroups": adgroups}
    if dry_run:
        return {"dry_run": True, "method": "adgroups.update", "params": payload}
    result = (client or default_client()).call("adgroups", "update", payload)
    append_audit(action="update", object_type="adgroups", object_id=[g.get("Id") for g in adgroups], new_value=adgroups, reason=reason, api_status="ok")
    return result


def ads_update(client: AdsClient, ads_items: list[dict], *, dry_run: bool = False, reason: str = "") -> dict:
    payload = {"Ads": ads_items}
    if dry_run:
        return {"dry_run": True, "method": "ads.update", "params": payload}
    result = (client or default_client()).call("ads", "update", payload)
    append_audit(action="update", object_type="ads", object_id=[a.get("Id") for a in ads_items], new_value=ads_items, reason=reason, api_status="ok")
    return result


def bidmodifiers_set(client: AdsClient, modifiers: list[dict], *, dry_run: bool = False, reason: str = "") -> dict:
    payload = {"BidModifiers": modifiers}
    if dry_run:
        return {"dry_run": True, "method": "bidmodifiers.set", "params": payload}
    result = (client or default_client()).call("bidmodifiers", "set", payload)
    append_audit(action="set", object_type="bidmodifiers", object_id=[m.get("Id") for m in modifiers], new_value=modifiers, reason=reason, api_status="ok")
    return result


def bidmodifiers_delete(client: AdsClient, ids: list[int], *, dry_run: bool = False, reason: str = "") -> dict:
    payload = {"SelectionCriteria": {"Ids": ids}}
    if dry_run:
        return {"dry_run": True, "method": "bidmodifiers.delete", "params": payload}
    result = (client or default_client()).call("bidmodifiers", "delete", payload)
    append_audit(action="delete", object_type="bidmodifiers", object_id=ids, reason=reason, api_status="ok")
    return result


def campaign_suspend(client: AdsClient | None = None, campaign_id: int | None = None, *, dry_run: bool = False, reason: str = "") -> dict:
    ads = client or default_client()
    cid = campaign_id or _managed_id()
    _assert_not_old_master(cid)
    payload = {"SelectionCriteria": {"Ids": [cid]}}
    if dry_run:
        return {"dry_run": True, "method": "campaigns.suspend", "params": payload}
    result = ads.call("campaigns", "suspend", payload)
    append_audit(action="suspend", object_type="campaign", object_id=cid, reason=reason or "manual suspend", api_status="ok")
    return result


def campaign_resume(client: AdsClient | None = None, campaign_id: int | None = None, *, dry_run: bool = False, reason: str = "") -> dict:
    ads = client or default_client()
    cid = campaign_id or _managed_id()
    _assert_not_old_master(cid)
    payload = {"SelectionCriteria": {"Ids": [cid]}}
    if dry_run:
        return {"dry_run": True, "method": "campaigns.resume", "params": payload}
    result = ads.call("campaigns", "resume", payload)
    append_audit(action="resume", object_type="campaign", object_id=cid, reason=reason or "manual resume", api_status="ok")
    return result


def campaign_update(client: AdsClient, campaign: dict, *, dry_run: bool = False, reason: str = "") -> dict:
    cid = campaign["Id"]
    _assert_not_old_master(cid)
    payload = {"Campaigns": [campaign]}
    if dry_run:
        return {"dry_run": True, "method": "campaigns.update", "params": payload}
    old = campaigns_get(client, [cid])
    result = client.call("campaigns", "update", payload)
    append_audit(
        action="update",
        object_type="campaign",
        object_id=cid,
        old_value=old[0] if old else None,
        new_value=campaign,
        reason=reason,
        api_status="ok",
    )
    return result


def status(client: AdsClient | None = None) -> dict:
    ads = client or default_client()
    state = load_state()
    cid = state.get("current_managed_campaign_id")
    today = date.today()
    start = state.get("campaign_creation_date") or today.isoformat()
    old_period = state["baseline"]["period"]
    out: dict[str, Any] = {
        "state_file": "ads/state.json",
        "old_master_campaign_id": OLD_MASTER_CAMPAIGN_ID,
        "current_managed_campaign_id": cid,
        "allowed_test_budget_rub": state.get("max_experiment_spend_rub") or MAX_EXPERIMENT_SPEND_RUB,
        "baseline": state.get("baseline"),
    }
    old_spend = campaign_report(ads, OLD_MASTER_CAMPAIGN_ID, date_from=today.isoformat(), date_to=today.isoformat())
    out["old_master_today"] = {
        "cost_rub": _sum_cost(old_spend),
        "impressions": _sum_field(old_spend, "Impressions"),
        "clicks": _sum_field(old_spend, "Clicks"),
    }
    if not cid:
        out["note"] = "No managed campaign yet"
        return out
    camps = campaigns_get(ads, [cid])
    ads_rows = ads_get(ads, campaign_ids=[cid])
    groups = adgroups_get(ads, campaign_ids=[cid])
    kws = keywords_get(ads, campaign_ids=[cid])
    bms = bidmodifiers_get(ads, campaign_ids=[cid])
    direct = campaign_report(ads, cid, date_from=start, date_to=today.isoformat())
    cost = _sum_cost(direct)
    clicks = _sum_field(direct, "Clicks")
    impressions = _sum_field(direct, "Impressions")
    quality = metrika_campaign_quality(ads, cid, date_from=start, date_to=today.isoformat())
    remaining = max(0.0, MAX_EXPERIMENT_SPEND_RUB - cost)
    devices = []
    placements = []
    queries = []
    try:
        devices = sliced_report(
            ads,
            cid,
            date_from=start,
            date_to=today.isoformat(),
            field_names=["Device", "Impressions", "Clicks", "Cost"],
        )
    except DirectApiError as exc:
        devices = [{"error": str(exc)}]
    try:
        placements = sliced_report(
            ads,
            cid,
            date_from=start,
            date_to=today.isoformat(),
            field_names=["AdNetworkType", "Impressions", "Clicks", "Cost"],
        )
    except DirectApiError as exc:
        placements = [{"error": str(exc)}]
    try:
        queries = search_query_report(ads, cid, date_from=start, date_to=today.isoformat())[:15]
    except DirectApiError:
        queries = []
    try:
        sites = metrika_breakdown(ads, cid, date_from=start, date_to=today.isoformat(), dimension="ym:s:lastDirectClickPlatform")
    except DirectApiError:
        sites = []
    try:
        device_quality = metrika_breakdown(ads, cid, date_from=start, date_to=today.isoformat(), dimension="ym:s:deviceCategory")
    except DirectApiError:
        device_quality = []
    cpa_start = cost / quality["game_start"] if quality["game_start"] else None
    cpa_complete = cost / quality["game_complete"] if quality["game_complete"] else None
    cpa_activated = cost / quality["activated"] if quality["activated"] else None
    device_users = {row.get("name"): row.get("users", 0) for row in device_quality}
    total_device_users = sum(float(value or 0) for value in device_users.values())
    tablet_share = (
        float(device_users.get("Tablets", 0) or 0) / total_device_users
        if total_device_users else None
    )
    alerts = []
    if (camps[0] if camps else {}).get("State") == "ENDED":
        alerts.append("STOP: campaign has ended")
    if cost >= MAX_EXPERIMENT_SPEND_RUB:
        alerts.append("STOP: spend reached 500 ₽")
    if quality["game_complete"] == 0 and cost >= 200:
        alerts.append("STOP signal: ≥200 ₽ and 0 game_complete")
    if cpa_complete and cost >= 300 and cpa_complete > 120:
        alerts.append("STOP signal: ≥300 ₽ and CPA complete > 120 ₽")
    if quality["game_start"] == 0 and quality["visits"] >= 50:
        alerts.append("STOP signal: ≥50 visits and 0 game_start")
    if tablet_share is not None and tablet_share >= 0.5 and quality["game_start"] == 0:
        alerts.append("QUALITY signal: tablet share ≥50% with 0 game_start")
    if out["old_master_today"]["cost_rub"] > 1 and cost > 0:
        alerts.append("Old Master and new campaign both show spend today")
    out.update(
        {
            "campaign": camps[0] if camps else None,
            "adgroups": groups,
            "ads": [
                {
                    "Id": a.get("Id"),
                    "Status": a.get("Status"),
                    "State": a.get("State"),
                    "StatusClarification": a.get("StatusClarification"),
                    "Href": (a.get("TextAd") or {}).get("Href"),
                    "Title": (a.get("TextAd") or {}).get("Title"),
                }
                for a in ads_rows
            ],
            "keywords": [{"Id": k.get("Id"), "Keyword": k.get("Keyword"), "State": k.get("State")} for k in kws],
            "bidmodifiers": bms,
            "direct": {
                "spend_rub": cost,
                "remaining_rub": remaining,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": (clicks / impressions) if impressions else None,
                "cpc": (cost / clicks) if clicks else None,
                "tablet_share": tablet_share,
            },
            "metrika": quality,
            "cpa": {"start": cpa_start, "complete": cpa_complete, "activated": cpa_activated},
            "devices_direct": devices,
            "placements_direct": placements,
            "sites_metrika": sites,
            "devices_metrika": device_quality,
            "search_queries": queries,
            "baseline_compare": {
                "cpa_complete_baseline": state["baseline"]["cpa_complete_rub"],
                "cpa_complete_now": cpa_complete,
                "cpa_complete_desktop_baseline": state["baseline"]["cpa_complete_desktop_rub"],
            },
            "alerts": alerts,
        }
    )
    return out


def _managed_id() -> int:
    cid = load_state().get("current_managed_campaign_id")
    if not cid:
        raise ConfigError("No current_managed_campaign_id in ads/state.json")
    return int(cid)


def _assert_not_old_master(campaign_id: int) -> None:
    if int(campaign_id) == OLD_MASTER_CAMPAIGN_ID:
        raise ConfigError("Refusing to mutate old Master campaign 713889884")


def _match_product_goals(goals: list[dict]) -> dict:
    by_id = {int(g.get("id")): g for g in goals if g.get("id") is not None}
    found = {}
    missing = []
    for key, gid in PRODUCT_GOALS.items():
        goal = by_id.get(int(gid))
        found[key] = {"id": gid, "name": (goal or {}).get("name"), "present": bool(goal)}
        if not goal:
            missing.append(key)
    alias_ok = True
    for gid, names in GOAL_NAME_ALIASES.items():
        goal = by_id.get(int(gid))
        if not goal:
            alias_ok = False
            continue
        name = (goal.get("name") or "")
        if not any(alias.lower() in name.lower() for alias in names):
            alias_ok = False
    return {
        "ok": not missing and alias_ok,
        "found": found,
        "missing": missing,
        "all_goals": [{"id": g.get("id"), "name": g.get("name"), "type": g.get("type")} for g in goals],
    }


def _public_client(client: dict) -> dict:
    return {
        "ClientId": client.get("ClientId"),
        "Login": client.get("Login"),
        "Type": client.get("Type"),
        "Currency": client.get("Currency"),
        "AvailableCampaignTypes": client.get("AvailableCampaignTypes"),
        "Restrictions": client.get("Restrictions"),
        "Grants": client.get("Grants"),
        "VatRate": client.get("VatRate"),
        "Archived": client.get("Archived"),
    }


def _sum_cost(rows: list[dict]) -> float:
    return sum(parse_report_money(r.get("Cost")) for r in rows)


def _sum_field(rows: list[dict], name: str) -> float:
    return sum(parse_report_money(r.get(name)) for r in rows)


def _num(values, index: int) -> float:
    try:
        return float(values[index] or 0)
    except (IndexError, TypeError, ValueError):
        return 0.0


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
