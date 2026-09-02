#!/usr/bin/env python
"""Read-only production extract and metric builder for the August 2026 audit.

Run through scripts/with_rds.sh so Django receives production DB settings.  The
database session is explicitly read-only; only local CSV/JSON files are written.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interoves_django.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402


MSK = ZoneInfo("Europe/Moscow")
UTC = dt_timezone.utc
AUG_START = datetime(2026, 8, 1, tzinfo=MSK)
AUG_END = datetime(2026, 9, 1, tzinfo=MSK)
OUT_DIR = Path("reports/analytics/2026-08")


def utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_day(value):
    return aware(value).astimezone(MSK).date() if value else None


def actor_raw(row):
    if row.get("user_id") is not None:
        return ("user", int(row["user_id"]))
    if row.get("anon_key"):
        return ("anon", str(row["anon_key"]))
    if row.get("team_id") is not None:
        return ("team", str(row["team_id"]))
    return None


def actor_label(actor):
    return "{}:{}".format(actor[0][0], actor[1]) if actor else "unknown"


def mean(values):
    values = list(values)
    return statistics.fmean(values) if values else None


def median(values):
    values = list(values)
    return statistics.median(values) if values else None


def pct(numerator, denominator):
    return (100.0 * numerator / denominator) if denominator else None


def round_or_none(value, digits=3):
    return round(value, digits) if value is not None else None


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def game_kind(game_id):
    return {
        "ladder": "ladder",
        "alphabetty": "alphabetty",
        "salad": "salad",
        "replacements": "replacement",
    }.get(str(game_id), str(game_id))


def parse_json(raw, fallback=None):
    if raw in (None, ""):
        return {} if fallback is None else fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {} if fallback is None else fallback


def placement_publish_at(placement, game):
    if placement.get("difficulty_published_at"):
        return aware(placement["difficulty_published_at"])
    tags = parse_json(game.get("tags"))
    schedule = {
        "ladder": ("ladder_publish_start", 1),
        "alphabetty": ("alphabetty_publish_start", 1),
        "salad": ("word_salad_publish_start", 1),
        "week_task": ("week_task_publish_start", 7),
    }.get(str(placement["game_id"]))
    if not schedule:
        return aware(game.get("visible_start_time") or game.get("start_time"))
    raw = tags.get(schedule[0])
    try:
        number = int(str(placement["number"]).split(".")[0])
        start = datetime.fromisoformat(str(raw))
        if start.tzinfo is None:
            start = start.replace(tzinfo=MSK)
        return (start + timedelta(days=(number - 1) * schedule[1])).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def active_streaks(days):
    ordered = sorted(set(days))
    if not ordered:
        return []
    streaks = []
    current = 1
    for previous, today in zip(ordered, ordered[1:]):
        if today == previous + timedelta(days=1):
            current += 1
        else:
            streaks.append(current)
            current = 1
    streaks.append(current)
    return streaks


def bucket_count(value, buckets):
    for label, lower, upper in buckets:
        if value >= lower and (upper is None or value <= upper):
            return label
    raise AssertionError(value)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute("SELECT UTC_TIMESTAMP(6)")
        as_of = aware(cursor.fetchone()[0])

    complete_through = as_of.astimezone(MSK).date() - timedelta(days=1)
    aug_start_db = utc_naive(AUG_START)
    aug_end_db = utc_naive(AUG_END)
    as_of_db = as_of.replace(tzinfo=None)

    claims = {
        str(row["anon_key"]): int(row["user_id"])
        for row in query("SELECT anon_key, user_id FROM games_anonaccountclaim")
    }

    def canonical(actor):
        if actor and actor[0] == "anon" and actor[1] in claims:
            return ("user", claims[actor[1]])
        return actor

    users = {
        int(row["id"]): {
            "date_joined": aware(row["date_joined"]),
            "is_active": bool(row["is_active"]),
        }
        for row in query("SELECT id, date_joined, is_active FROM auth_user")
    }

    event_sql = """
        SELECT id, team_id, user_id, anon_key, game_id, task_group_id,
               game_kind, game_instance_id, public_game_id,
               {timestamp} AS event_at, is_backfilled
        FROM {table}
        WHERE {timestamp} < %s
        ORDER BY {timestamp}, id
    """
    starts = query(
        event_sql.format(
            timestamp="started_at", table="games_playerstartedgame"
        ),
        (as_of_db,),
    )
    completes = query(
        event_sql.format(
            timestamp="completed_at", table="games_playercompletedgame"
        ),
        (as_of_db,),
    )
    for collection in (starts, completes):
        for row in collection:
            row["event_at"] = aware(row["event_at"])
            row["actor"] = canonical(actor_raw(row))
            row["day"] = local_day(row["event_at"])
            row["kind"] = game_kind(row["game_id"])
            row["instance_key"] = (row["actor"], str(row["game_instance_id"]))

    live_starts = [row for row in starts if not row["is_backfilled"]]
    live_completes = [row for row in completes if not row["is_backfilled"]]
    august_starts = [row for row in live_starts if date(2026, 8, 1) <= row["day"] <= date(2026, 8, 31)]
    august_completes = [row for row in live_completes if date(2026, 8, 1) <= row["day"] <= date(2026, 8, 31)]

    # Persisted attempts are a distinct entity. Keep production traffic small:
    # aggregate the indexed month range in SQL instead of transferring 170k+
    # individual rows over the SSM tunnel.
    attempt_daily_counts = {
        row["day"]: int(row["attempts"])
        for row in query(
            """
            SELECT DATE(DATE_ADD(time, INTERVAL 3 HOUR)) day, COUNT(*) attempts
            FROM games_attempt
            WHERE skip = 0 AND time >= %s AND time < %s
            GROUP BY DATE(DATE_ADD(time, INTERVAL 3 HOUR))
            """,
            (aug_start_db, aug_end_db),
        )
    }
    attempt_actors = set()
    for kind, column in (("user", "user_id"), ("anon", "anon_key"), ("team", "team_id")):
        rows = query(
            """SELECT {column} actor_key
               FROM games_attempt
               WHERE skip=0 AND time >= %s AND time < %s AND {column} IS NOT NULL
               GROUP BY {column}""".format(column=column),
            (aug_start_db, aug_end_db),
        )
        for row in rows:
            key = int(row["actor_key"]) if kind == "user" else str(row["actor_key"])
            attempt_actors.add(canonical((kind, key)))

    # Exact saved-Attempt count between each observed start and its completion.
    # The task+actor+time indexes service these joins; results are one row per
    # started instance, not raw attempt payloads.
    attempt_counts_to_complete = {}
    actor_join_specs = (
        ("user", "s.user_id IS NOT NULL", "c.user_id=s.user_id AND c.team_id IS NULL AND c.anon_key IS NULL", "a.user_id=s.user_id AND a.team_id IS NULL AND a.anon_key IS NULL"),
        ("anon", "s.anon_key IS NOT NULL", "c.anon_key=s.anon_key AND c.team_id IS NULL AND c.user_id IS NULL", "a.anon_key=s.anon_key AND a.team_id IS NULL AND a.user_id IS NULL"),
        ("team", "s.team_id IS NOT NULL", "c.team_id=s.team_id AND c.user_id IS NULL AND c.anon_key IS NULL", "a.team_id=s.team_id AND a.user_id IS NULL AND a.anon_key IS NULL"),
    )
    for _kind, actor_filter, completion_actor_join, attempt_actor_join in actor_join_specs:
        rows = query(
            """
            SELECT s.id start_id, COUNT(a.id) attempt_count
            FROM games_playerstartedgame s
            JOIN games_playercompletedgame c
              ON c.game_instance_id=s.game_instance_id
             AND {completion_actor_join}
             AND c.is_backfilled=0
             AND c.completed_at >= s.started_at
            JOIN games_task t ON t.task_group_id=s.task_group_id
            LEFT JOIN games_attempt a
              ON a.task_id=t.id
             AND a.game_id=s.game_id
             AND a.skip=0
             AND {attempt_actor_join}
             AND a.time >= s.started_at
             AND a.time <= c.completed_at
            WHERE s.is_backfilled=0
              AND {actor_filter}
              AND s.started_at >= %s AND s.started_at < %s
            GROUP BY s.id
            """.format(
                completion_actor_join=completion_actor_join,
                attempt_actor_join=attempt_actor_join,
                actor_filter=actor_filter,
            ),
            (aug_start_db, aug_end_db),
        )
        attempt_counts_to_complete.update({int(row["start_id"]): int(row["attempt_count"]) for row in rows})

    # Status totals are small grouped results and make the Attempt definition
    # auditable without exposing submitted text.
    attempt_status_counts = {
        str(row["status"]): int(row["n"])
        for row in query(
            """
            SELECT status, COUNT(*) n
            FROM games_attempt
            WHERE skip=0 AND time >= %s AND time < %s
            GROUP BY status
            """,
            (aug_start_db, aug_end_db),
        )
    }

    # Earliest observable playing interaction, using existing Attempt and real
    # HintAttempt rows.  This is used only to avoid calling returning users new.
    first_interaction = {}
    first_queries = (
        ("user", "SELECT user_id actor_key, MIN(time) first_at FROM games_attempt WHERE skip=0 AND user_id IS NOT NULL GROUP BY user_id"),
        ("anon", "SELECT anon_key actor_key, MIN(time) first_at FROM games_attempt WHERE skip=0 AND anon_key IS NOT NULL GROUP BY anon_key"),
        ("team", "SELECT team_id actor_key, MIN(time) first_at FROM games_attempt WHERE skip=0 AND team_id IS NOT NULL GROUP BY team_id"),
        ("user", "SELECT user_id actor_key, MIN(time) first_at FROM games_hintattempt WHERE is_real_request=1 AND user_id IS NOT NULL GROUP BY user_id"),
        ("anon", "SELECT anon_key actor_key, MIN(time) first_at FROM games_hintattempt WHERE is_real_request=1 AND anon_key IS NOT NULL GROUP BY anon_key"),
        ("team", "SELECT team_id actor_key, MIN(time) first_at FROM games_hintattempt WHERE is_real_request=1 AND team_id IS NOT NULL GROUP BY team_id"),
    )
    for kind, sql in first_queries:
        for row in query(sql):
            key = int(row["actor_key"]) if kind == "user" else str(row["actor_key"])
            actor = canonical((kind, key))
            first_at = aware(row["first_at"])
            if first_at and (actor not in first_interaction or first_at < first_interaction[actor]):
                first_interaction[actor] = first_at
    for row in live_starts:
        actor = row["actor"]
        if actor and (actor not in first_interaction or row["event_at"] < first_interaction[actor]):
            first_interaction[actor] = row["event_at"]

    starts_by_actor = defaultdict(list)
    completes_by_actor = defaultdict(list)
    for row in live_starts:
        starts_by_actor[row["actor"]].append(row)
    for row in live_completes:
        completes_by_actor[row["actor"]].append(row)

    new_player_first = {}
    for actor, rows in starts_by_actor.items():
        first_start = min(rows, key=lambda row: (row["event_at"], row["id"]))
        interaction = first_interaction.get(actor, first_start["event_at"])
        if (
            AUG_START <= interaction.astimezone(MSK) < AUG_END
            and AUG_START <= first_start["event_at"].astimezone(MSK) < AUG_END
        ):
            new_player_first[actor] = first_start

    signup_states = query(
        """
        SELECT team_id, user_id, anon_key, signup_at, signup_method,
               activated_at, activation_is_backfilled
        FROM games_playeranalyticsstate
        """
    )
    for row in signup_states:
        row["actor"] = canonical(actor_raw(row))
        row["signup_at"] = aware(row["signup_at"])
        row["activated_at"] = aware(row["activated_at"])

    auth_registrations = [
        (user_id, data["date_joined"])
        for user_id, data in users.items()
        if AUG_START <= data["date_joined"].astimezone(MSK) < AUG_END
    ]

    signup_by_day = Counter(
        local_day(row["signup_at"])
        for row in signup_states
        if row["signup_at"] and AUG_START <= row["signup_at"].astimezone(MSK) < AUG_END
    )
    activation_by_day = Counter(
        local_day(row["activated_at"])
        for row in signup_states
        if row["activated_at"]
        and not row["activation_is_backfilled"]
        and AUG_START <= row["activated_at"].astimezone(MSK) < AUG_END
    )

    starts_by_day = defaultdict(list)
    completes_by_day = defaultdict(list)
    for row in august_starts:
        starts_by_day[row["day"]].append(row)
    for row in august_completes:
        completes_by_day[row["day"]].append(row)

    registration_by_day = Counter(local_day(joined) for _, joined in auth_registrations)
    new_players_by_day = Counter(row["day"] for row in new_player_first.values())

    def registered_at(actor, moment):
        if not actor or actor[0] != "user":
            return False
        joined = users.get(int(actor[1]), {}).get("date_joined")
        return bool(joined and joined <= aware(moment))

    daily_rows = []
    previous_start_actors = set()
    for offset in range(31):
        day = date(2026, 8, 1) + timedelta(days=offset)
        srows = starts_by_day[day]
        crows = completes_by_day[day]
        start_actors = {row["actor"] for row in srows if row["actor"]}
        complete_actors = {row["actor"] for row in crows if row["actor"]}
        registered = {
            row["actor"] for row in srows
            if row["actor"] and registered_at(row["actor"], row["event_at"])
        }
        anonymous = {
            row["actor"] for row in srows
            if row["actor"] and row["actor"][0] != "team"
            and not registered_at(row["actor"], row["event_at"])
        }
        teams = {actor for actor in start_actors if actor[0] == "team"}
        coverage = (
            "pre_start_instrumentation" if day < date(2026, 8, 15)
            else "partial_from_16_49_msk" if day == date(2026, 8, 15)
            else "observed_full_day"
        )
        complete_coverage = (
            "pre_complete_instrumentation" if day < date(2026, 8, 10)
            else "partial_from_22_20_msk" if day == date(2026, 8, 10)
            else "observed_full_day"
        )
        daily_rows.append({
            "date": day.isoformat(),
            "dau": "",
            "dau_status": "unavailable_no_visit_identity",
            "new_users": "",
            "new_users_status": "unavailable_no_visit_identity",
            "new_registrations": registration_by_day[day],
            "unique_players": len(start_actors) if coverage != "pre_start_instrumentation" else "",
            "registered_players": len(registered) if coverage != "pre_start_instrumentation" else "",
            "anonymous_players": len(anonymous) if coverage != "pre_start_instrumentation" else "",
            "team_actors": len(teams) if coverage != "pre_start_instrumentation" else "",
            "game_start": len(srows) if coverage != "pre_start_instrumentation" else "",
            "game_complete": len(crows) if complete_coverage != "pre_complete_instrumentation" else "",
            "completed_users": len(complete_actors) if complete_coverage != "pre_complete_instrumentation" else "",
            "signup": signup_by_day[day] if day >= date(2026, 8, 15) else "",
            "activated_player": activation_by_day[day] if day >= date(2026, 8, 15) else "",
            "first_time_players": new_players_by_day[day] if coverage != "pre_start_instrumentation" else "",
            "returned_from_previous_day": len(start_actors & previous_start_actors) if coverage == "observed_full_day" and day > date(2026, 8, 16) else "",
            "persisted_attempts": attempt_daily_counts.get(day, 0),
            "start_coverage": coverage,
            "complete_coverage": complete_coverage,
        })
        previous_start_actors = start_actors if coverage != "pre_start_instrumentation" else set()

    daily_fields = list(daily_rows[0])
    write_csv(OUT_DIR / "daily_metrics.csv", daily_rows, daily_fields)

    daily_kind_rows = []
    for offset in range(31):
        day = date(2026, 8, 1) + timedelta(days=offset)
        kinds = sorted({row["kind"] for row in starts_by_day[day]} | {row["kind"] for row in completes_by_day[day]})
        for kind in kinds:
            srows = [row for row in starts_by_day[day] if row["kind"] == kind]
            crows = [row for row in completes_by_day[day] if row["kind"] == kind]
            daily_kind_rows.append({
                "date": day.isoformat(),
                "game_type": kind,
                "unique_start_players": len({row["actor"] for row in srows if row["actor"]}),
                "game_start": len(srows),
                "unique_complete_players": len({row["actor"] for row in crows if row["actor"]}),
                "game_complete": len(crows),
            })
    write_csv(
        OUT_DIR / "daily_game_type_metrics.csv",
        daily_kind_rows,
        list(daily_kind_rows[0]) if daily_kind_rows else ["date", "game_type"],
    )

    # Cohort retention: new observable players only; exact-day and rolling.
    cohort_actors = defaultdict(set)
    for actor, row in new_player_first.items():
        cohort_actors[row["day"]].add(actor)
    start_days_by_actor = {
        actor: {row["day"] for row in rows}
        for actor, rows in starts_by_actor.items()
    }
    complete_days_by_actor = {
        actor: {row["day"] for row in rows}
        for actor, rows in completes_by_actor.items()
    }
    retention_days = (1, 2, 3, 7, 14, 21, 30)
    cohort_rows = []
    for cohort_day in sorted(cohort_actors):
        actors = cohort_actors[cohort_day]
        row = {"cohort_date": cohort_day.isoformat(), "cohort_size": len(actors)}
        for n in retention_days:
            target = cohort_day + timedelta(days=n)
            observed = target <= complete_through
            for prefix, activity in (("start", start_days_by_actor), ("complete", complete_days_by_actor)):
                count = sum(target in activity.get(actor, set()) for actor in actors) if observed else None
                row["d{}_{}_users".format(n, prefix)] = count if observed else ""
                row["d{}_{}_pct".format(n, prefix)] = round_or_none(pct(count, len(actors)), 2) if observed else ""
            rolling_count = (
                sum(any(day >= target for day in start_days_by_actor.get(actor, set())) for actor in actors)
                if observed else None
            )
            row["rolling_d{}_start_users".format(n)] = rolling_count if observed else ""
            row["rolling_d{}_start_pct".format(n)] = round_or_none(pct(rolling_count, len(actors)), 2) if observed else ""
        cohort_rows.append(row)
    cohort_fields = ["cohort_date", "cohort_size"]
    for n in retention_days:
        cohort_fields.extend([
            "d{}_start_users".format(n), "d{}_start_pct".format(n),
            "d{}_complete_users".format(n), "d{}_complete_pct".format(n),
            "rolling_d{}_start_users".format(n), "rolling_d{}_start_pct".format(n),
        ])
    write_csv(OUT_DIR / "retention_cohorts.csv", cohort_rows, cohort_fields)

    def aggregate_retention(n, activity):
        eligible = []
        retained = 0
        for cohort_day, actors in cohort_actors.items():
            target = cohort_day + timedelta(days=n)
            if target > complete_through:
                continue
            eligible.extend(actors)
            retained += sum(target in activity.get(actor, set()) for actor in actors)
        return {"users": retained, "eligible": len(eligible), "pct": round_or_none(pct(retained, len(eligible)), 2)}

    def aggregate_rolling(n, activity):
        eligible = []
        retained = 0
        for cohort_day, actors in cohort_actors.items():
            target = cohort_day + timedelta(days=n)
            if target > complete_through:
                continue
            eligible.extend(actors)
            retained += sum(any(day >= target for day in activity.get(actor, set())) for actor in actors)
        return {"users": retained, "eligible": len(eligible), "pct": round_or_none(pct(retained, len(eligible)), 2)}

    retention_summary = {
        "start": {"D{}".format(n): aggregate_retention(n, start_days_by_actor) for n in retention_days},
        "complete": {"D{}".format(n): aggregate_retention(n, complete_days_by_actor) for n in retention_days},
        "rolling_start": {"D{}".format(n): aggregate_rolling(n, start_days_by_actor) for n in (1, 7, 14)},
    }

    retention_by_identity = {}
    for segment in ("registered", "anonymous"):
        retention_by_identity[segment] = {}
        for n in (1, 7):
            eligible = retained = 0
            for actor, first in new_player_first.items():
                actor_segment = "registered" if registered_at(actor, first["event_at"]) else "anonymous"
                target = first["day"] + timedelta(days=n)
                if actor_segment != segment or target > complete_through:
                    continue
                eligible += 1
                retained += target in start_days_by_actor.get(actor, set())
            retention_by_identity[segment]["D{}".format(n)] = {
                "users": retained, "eligible": eligible,
                "pct": round_or_none(pct(retained, eligible), 2),
            }
    retention_summary["by_identity_at_first_start"] = retention_by_identity

    # Available part of the first-touch funnel. Client-only landing/onboarding
    # steps remain unavailable and are documented separately.
    state_by_actor = {row["actor"]: row for row in signup_states if row["actor"]}
    funnel_counts = Counter()
    funnel_counts["game_start"] = len(new_player_first)
    within7_eligible = 0
    for actor, first in new_player_first.items():
        after_completes = [
            row for row in completes_by_actor.get(actor, [])
            if row["event_at"] >= first["event_at"]
        ]
        funnel_counts["game_complete"] += len(after_completes) >= 1
        funnel_counts["second_game_complete"] += len(after_completes) >= 2
        funnel_counts["third_game_complete"] += len(after_completes) >= 3
        joined = users.get(int(actor[1]), {}).get("date_joined") if actor[0] == "user" else None
        funnel_counts["registered_at_first_start"] += registered_at(actor, first["event_at"])
        funnel_counts["signup_after_first_start"] += bool(joined and joined >= first["event_at"] and joined < as_of)
        state = state_by_actor.get(actor)
        funnel_counts["activated_player"] += bool(
            state and state["activated_at"] and not state["activation_is_backfilled"]
            and state["activated_at"] >= first["event_at"]
        )
        target_d1 = first["day"] + timedelta(days=1)
        if target_d1 <= complete_through:
            funnel_counts["d1_eligible"] += 1
            funnel_counts["next_day_return"] += target_d1 in start_days_by_actor.get(actor, set())
        target_d7 = first["day"] + timedelta(days=7)
        if target_d7 <= complete_through:
            within7_eligible += 1
            funnel_counts["return_within_7_days"] += any(
                first["day"] < day <= target_d7 for day in start_days_by_actor.get(actor, set())
            )
    funnel_counts["within7_eligible"] = within7_eligible

    # Frequency and habit among observed August starters.
    august_player_actors = {row["actor"] for row in august_starts if row["actor"]}
    august_active_days = {
        actor: sorted({row["day"] for row in august_starts if row["actor"] == actor})
        for actor in august_player_actors
    }
    active_day_buckets = Counter()
    max_streak_by_actor = {}
    all_gaps = []
    for actor, days in august_active_days.items():
        active_day_buckets[bucket_count(len(days), (
            ("1", 1, 1), ("2", 2, 2), ("3-4", 3, 4), ("5-7", 5, 7),
            ("8-14", 8, 14), ("15-20", 15, 20), ("21+", 21, None),
        ))] += 1
        gaps = [(today - previous).days for previous, today in zip(days, days[1:])]
        all_gaps.extend(gaps)
        max_streak_by_actor[actor] = max(active_streaks(days) or [0])
    streak_distribution = Counter(max_streak_by_actor.values())

    # Completion distributions use the union of observed starters/completers.
    observed_actors = august_player_actors | {row["actor"] for row in august_completes if row["actor"]}
    starts_per_actor = Counter(row["actor"] for row in august_starts if row["actor"])
    completes_per_actor = Counter(row["actor"] for row in august_completes if row["actor"])
    completion_buckets = (("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3", 3, 3),
                          ("4-5", 4, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21+", 21, None))
    completion_distribution = defaultdict(Counter)
    for actor in observed_actors:
        label = bucket_count(completes_per_actor[actor], completion_buckets)
        completion_distribution["all"][label] += 1
        completion_distribution[actor[0]][label] += 1

    # Game-type metrics and first-three-game sequences.
    starts_by_kind = defaultdict(list)
    completes_by_kind = defaultdict(list)
    for row in august_starts:
        starts_by_kind[row["kind"]].append(row)
    for row in august_completes:
        completes_by_kind[row["kind"]].append(row)

    game_type_rows = []
    for kind in sorted(set(starts_by_kind) | set(completes_by_kind)):
        srows = starts_by_kind[kind]
        crows = completes_by_kind[kind]
        players = {row["actor"] for row in srows if row["actor"]}
        s_counts = Counter(row["actor"] for row in srows if row["actor"])
        c_counts = Counter(row["actor"] for row in crows if row["actor"])
        first_by_actor = {}
        for row in srows:
            actor = row["actor"]
            if actor and (actor not in first_by_actor or row["event_at"] < first_by_actor[actor]["event_at"]):
                first_by_actor[actor] = row
        continued = 0
        d1_eligible = d1_users = d7_eligible = d7_users = 0
        for actor, first in first_by_actor.items():
            later = [row for row in starts_by_actor[actor] if row["event_at"] > first["event_at"]]
            continued += bool(later)
            for n in (1, 7):
                target = first["day"] + timedelta(days=n)
                if target <= complete_through:
                    if n == 1:
                        d1_eligible += 1
                        d1_users += target in start_days_by_actor.get(actor, set())
                    else:
                        d7_eligible += 1
                        d7_users += target in start_days_by_actor.get(actor, set())
        first_product_count = sum(first["kind"] == kind for first in new_player_first.values())
        game_type_rows.append({
            "game_type": kind,
            "unique_players": len(players),
            "game_start": len(srows),
            "game_complete": len(crows),
            "completion_rate_pct": round_or_none(pct(len(crows), len(srows)), 2),
            "unique_placements_started": len({row["game_instance_id"] for row in srows}),
            "avg_starts_per_player": round_or_none(mean(s_counts.values())),
            "avg_completes_per_player": round_or_none(mean(c_counts.get(actor, 0) for actor in players)),
            "continued_after_first_users": continued,
            "continued_after_first_pct": round_or_none(pct(continued, len(first_by_actor)), 2),
            "d1_users": d1_users,
            "d1_eligible": d1_eligible,
            "d1_pct": round_or_none(pct(d1_users, d1_eligible), 2),
            "d7_users": d7_users,
            "d7_eligible": d7_eligible,
            "d7_pct": round_or_none(pct(d7_users, d7_eligible), 2),
            "first_product_game_for_new_players": first_product_count,
        })
    write_csv(OUT_DIR / "game_type_metrics.csv", game_type_rows, list(game_type_rows[0]) if game_type_rows else ["game_type"])

    sequences = Counter()
    for actor in new_player_first:
        ordered = sorted(starts_by_actor[actor], key=lambda row: (row["event_at"], row["id"]))[:3]
        sequences[" → ".join(row["kind"] for row in ordered)] += 1

    # Placement metadata and metrics.
    games = {
        str(row["id"]): row
        for row in query("SELECT id, name, start_time, visible_start_time, tags FROM games_game")
    }
    placements = query(
        """
        SELECT p.id, p.game_id, p.task_group_id, p.number, p.name,
               d.n difficulty_n, d.stars difficulty_stars,
               d.published_at difficulty_published_at, d.calculated_at difficulty_calculated_at
        FROM games_gametaskgroup p
        LEFT JOIN games_dailygamedifficulty d ON d.placement_id = p.id
        """
    )
    placement_meta = {}
    august_placements = []
    for placement in placements:
        game = games.get(str(placement["game_id"]), {})
        published_at = placement_publish_at(placement, game)
        placement["published_at"] = published_at
        key = (str(placement["game_id"]), int(placement["task_group_id"]))
        placement_meta[key] = placement
        if published_at and AUG_START <= published_at.astimezone(MSK) < AUG_END:
            august_placements.append(placement)

    complete_by_instance = {}
    for row in live_completes:
        existing = complete_by_instance.get(row["instance_key"])
        if existing is None or row["event_at"] < existing["event_at"]:
            complete_by_instance[row["instance_key"]] = row

    placement_rows = []
    for placement in sorted(august_placements, key=lambda row: (row["published_at"], str(row["game_id"]), str(row["number"]))):
        game_id = str(placement["game_id"])
        task_group_id = int(placement["task_group_id"])
        srows = [row for row in august_starts if str(row["game_id"]) == game_id and int(row["task_group_id"]) == task_group_id]
        crows = [row for row in august_completes if str(row["game_id"]) == game_id and int(row["task_group_id"]) == task_group_id]
        starter_actors = {row["actor"] for row in srows if row["actor"]}
        completer_actors = {row["actor"] for row in crows if row["actor"]}
        solve_seconds = []
        attempt_counts = []
        cohort_completers = set()
        new_actors = set()
        new_continued = set()
        d1_eligible = d1_users = d7_eligible = d7_users = 0
        for start in srows:
            actor = start["actor"]
            completion = complete_by_instance.get(start["instance_key"])
            if completion and completion["event_at"] >= start["event_at"]:
                cohort_completers.add(actor)
                solve_seconds.append((completion["event_at"] - start["event_at"]).total_seconds())
                attempt_counts.append(attempt_counts_to_complete.get(int(start["id"]), 0))
            if actor in new_player_first and new_player_first[actor]["id"] == start["id"]:
                new_actors.add(actor)
                if any(row["event_at"] > start["event_at"] for row in starts_by_actor[actor]):
                    new_continued.add(actor)
                for n in (1, 7):
                    target = start["day"] + timedelta(days=n)
                    if target <= complete_through:
                        if n == 1:
                            d1_eligible += 1
                            d1_users += target in start_days_by_actor.get(actor, set())
                        else:
                            d7_eligible += 1
                            d7_users += target in start_days_by_actor.get(actor, set())
        placement_rows.append({
            "placement_id": placement["id"],
            "date": placement["published_at"].astimezone(MSK).date().isoformat(),
            "game_type": game_kind(game_id),
            "game_id": game_id,
            "public_game_id": placement["number"],
            "placement_name": placement["name"],
            "unique_starters": len(starter_actors),
            "unique_completers": len(completer_actors),
            "period_completion_rate_pct": round_or_none(pct(len(completer_actors), len(starter_actors)), 2),
            "starter_cohort_completers": len(cohort_completers),
            "starter_cohort_completion_pct": round_or_none(pct(len(cohort_completers), len(starter_actors)), 2),
            "mean_solve_seconds": round_or_none(mean(solve_seconds), 1),
            "median_solve_seconds": round_or_none(median(solve_seconds), 1),
            "mean_attempts_to_complete": round_or_none(mean(attempt_counts), 2),
            "median_attempts_to_complete": round_or_none(median(attempt_counts), 2),
            "difficulty_stars": placement["difficulty_stars"] if placement["difficulty_stars"] is not None else "",
            "difficulty_n": placement["difficulty_n"] if placement["difficulty_n"] is not None else "",
            "new_players": len(new_actors),
            "new_players_continued": len(new_continued),
            "new_players_continued_pct": round_or_none(pct(len(new_continued), len(new_actors)), 2),
            "d1_users": d1_users,
            "d1_eligible": d1_eligible,
            "d1_pct": round_or_none(pct(d1_users, d1_eligible), 2),
            "d7_users": d7_users,
            "d7_eligible": d7_eligible,
            "d7_pct": round_or_none(pct(d7_users, d7_eligible), 2),
            "start_coverage_from": "2026-08-15T16:49:44+03:00",
            "complete_coverage_from": "2026-08-10T22:20:23+03:00",
        })
    write_csv(OUT_DIR / "placement_metrics.csv", placement_rows, list(placement_rows[0]) if placement_rows else ["placement_id"])

    # Current-vs-archive for the three daily formats.
    daily_game_ids = {"ladder", "alphabetty", "salad"}
    current_archive = defaultdict(list)
    for row in august_starts:
        if str(row["game_id"]) not in daily_game_ids:
            continue
        meta = placement_meta.get((str(row["game_id"]), int(row["task_group_id"])))
        published_day = local_day(meta.get("published_at")) if meta else None
        status = "current" if published_day == row["day"] else "archive"
        current_archive[status].append(row)
    archive_summary = {}
    for status, srows in current_archive.items():
        actors = {row["actor"] for row in srows}
        instances = {row["instance_key"] for row in srows}
        matched = {key for key in instances if key in complete_by_instance}
        first_segment_start = {}
        for event in srows:
            actor = event["actor"]
            if actor and (
                actor not in first_segment_start
                or event["event_at"] < first_segment_start[actor]["event_at"]
            ):
                first_segment_start[actor] = event
        segment_retention = {}
        for n in (1, 7):
            eligible = retained = 0
            for actor, first in first_segment_start.items():
                target = first["day"] + timedelta(days=n)
                if target > complete_through:
                    continue
                eligible += 1
                retained += target in start_days_by_actor.get(actor, set())
            segment_retention["D{}".format(n)] = {
                "users": retained,
                "eligible": eligible,
                "pct": round_or_none(pct(retained, eligible), 2),
            }
        archive_summary[status] = {
            "starts": len(srows), "players": len(actors),
            "completes_for_started_instances": len(matched),
            "completion_pct": round_or_none(pct(len(matched), len(instances)), 2),
            "games_per_user": round_or_none(pct(len(srows), len(actors)) / 100 if actors else None),
            "retention_after_first_segment_start": segment_retention,
        }
    current_completer_actors = set()
    current_then_archive_actors = set()
    for row in august_completes:
        if str(row["game_id"]) not in daily_game_ids:
            continue
        meta = placement_meta.get((str(row["game_id"]), int(row["task_group_id"])))
        if not meta or local_day(meta.get("published_at")) != row["day"]:
            continue
        current_completer_actors.add(row["actor"])
        if any(
            later["event_at"] > row["event_at"]
            and later["day"] == row["day"]
            and str(later["game_id"]) in daily_game_ids
            and local_day(placement_meta.get((str(later["game_id"]), int(later["task_group_id"])), {}).get("published_at")) != later["day"]
            for later in starts_by_actor.get(row["actor"], [])
        ):
            current_then_archive_actors.add(row["actor"])

    first_complete_then_second_same_day = 0
    actors_with_aug_first_complete = 0
    for actor, rows in completes_by_actor.items():
        august_rows = [row for row in rows if date(2026, 8, 1) <= row["day"] <= date(2026, 8, 31)]
        if not august_rows:
            continue
        first = min(august_rows, key=lambda row: row["event_at"])
        actors_with_aug_first_complete += 1
        if any(row["event_at"] > first["event_at"] and row["day"] == first["day"] for row in starts_by_actor.get(actor, [])):
            first_complete_then_second_same_day += 1

    # Signup position among canonically linked players.
    completions_before_signup = []
    signup_conversion_buckets = defaultdict(lambda: [0, 0])
    for user_id, joined in auth_registrations:
        actor = ("user", user_id)
        before = sum(row["event_at"] < joined for row in completes_by_actor.get(actor, []))
        completions_before_signup.append(before)
    for actor in observed_actors:
        count = completes_per_actor[actor]
        if count <= 0:
            continue
        label = "1" if count == 1 else "2" if count == 2 else "3" if count == 3 else "4-5" if count <= 5 else "6+"
        signup_conversion_buckets[label][1] += 1
        if actor[0] == "user":
            joined = users.get(int(actor[1]), {}).get("date_joined")
            if joined and joined < AUG_END.astimezone(UTC):
                signup_conversion_buckets[label][0] += 1

    # Payments. TicketRequest is canonical for tickets; TributePurchase is not
    # added again because it links to the same TicketRequest.
    tickets = query(
        """
        SELECT id, created_by_id, team_id, money, tickets, time, currency,
               payment_provider, merchant, status, purchase_goal_queued_at
        FROM games_ticketrequest
        WHERE time < %s
        """,
        (aug_end_db,),
    )
    donations = query(
        """
        SELECT id, user_id, amount_rub, pay_amount, pay_currency,
               created_at, confirmed_at, status
        FROM games_donation
        WHERE created_at < %s OR confirmed_at < %s
        """,
        (aug_end_db, aug_end_db),
    )
    tribute_purchases = query(
        """
        SELECT id, amount, currency, purchase_created_at, received_at, status,
               matched_user_id, ticket_request_id
        FROM games_tributepurchase
        WHERE received_at < %s
        """,
        (aug_end_db,),
    )
    tribute_intents = query(
        """
        SELECT status, expected_amount, expected_currency, created_at
        FROM games_tributepaymentintent
        WHERE created_at >= %s AND created_at < %s
        """,
        (aug_start_db, aug_end_db),
    )
    august_tickets = [row for row in tickets if AUG_START <= aware(row["time"]).astimezone(MSK) < AUG_END]
    august_donations_created = [row for row in donations if AUG_START <= aware(row["created_at"]).astimezone(MSK) < AUG_END]
    august_donations_confirmed = [row for row in donations if row["confirmed_at"] and AUG_START <= aware(row["confirmed_at"]).astimezone(MSK) < AUG_END and row["status"] == "Confirmed"]
    ticket_revenue = Counter()
    ticket_revenue_by_provider = Counter()
    linked_revenue = Counter()
    linked_payers_by_currency = defaultdict(set)
    provider_status = Counter()
    successful_payment_events = []
    for row in august_tickets:
        provider_status[(row["payment_provider"], row["status"])] += 1
        if row["status"] == "Accepted":
            ticket_revenue[str(row["currency"])] += Decimal(row["money"])
            ticket_revenue_by_provider[(str(row["payment_provider"]), str(row["currency"]))] += Decimal(row["money"])
            if row["created_by_id"]:
                linked_revenue[str(row["currency"])] += Decimal(row["money"])
                linked_payers_by_currency[str(row["currency"])].add(("user", int(row["created_by_id"])))
                successful_payment_events.append((("user", int(row["created_by_id"])), aware(row["time"]), "ticket"))
    donation_revenue = Counter()
    for row in august_donations_confirmed:
        if row["pay_amount"] and row["pay_currency"]:
            try:
                donation_revenue[str(row["pay_currency"]).upper()] += Decimal(str(row["pay_amount"]))
            except Exception:
                pass
        if row["user_id"]:
            if row["pay_amount"] and row["pay_currency"]:
                try:
                    linked_revenue[str(row["pay_currency"]).upper()] += Decimal(str(row["pay_amount"]))
                    linked_payers_by_currency[str(row["pay_currency"]).upper()].add(("user", int(row["user_id"])))
                except Exception:
                    pass
            successful_payment_events.append((("user", int(row["user_id"])), aware(row["confirmed_at"]), "donation"))

    historical_payments = defaultdict(list)
    for row in tickets:
        if row["status"] == "Accepted" and row["created_by_id"]:
            historical_payments[("user", int(row["created_by_id"]))].append(aware(row["time"]))
    for row in donations:
        if row["status"] == "Confirmed" and row["confirmed_at"] and row["user_id"]:
            historical_payments[("user", int(row["user_id"]))].append(aware(row["confirmed_at"]))
    august_payers = {event[0] for event in successful_payment_events}
    first_time_payers = sum(min(historical_payments[actor]).astimezone(MSK) >= AUG_START for actor in august_payers)
    repeat_payers = sum(sum(moment.astimezone(MSK) < AUG_END for moment in historical_payments[actor]) >= 2 for actor in august_payers)

    # Power-user groups and core definitions.
    ranked = sorted(august_player_actors, key=lambda actor: (-completes_per_actor[actor], actor_label(actor)))
    paid_ever = set(historical_payments)
    power_groups = {}
    for label, fraction in (("top_1_pct", 0.01), ("top_5_pct", 0.05), ("top_10_pct", 0.10)):
        n = max(1, math.ceil(len(ranked) * fraction)) if ranked else 0
        group = ranked[:n]
        preferred = Counter()
        for actor in group:
            actor_kinds = Counter(row["kind"] for row in august_completes if row["actor"] == actor)
            if actor_kinds:
                preferred[actor_kinds.most_common(1)[0][0]] += 1
        power_groups[label] = {
            "users": len(group),
            "completed_games": sum(completes_per_actor[a] for a in group),
            "mean_completed_games": round_or_none(mean(completes_per_actor[a] for a in group)),
            "mean_active_days": round_or_none(mean(len(august_active_days[a]) for a in group)),
            "registered_current": sum(a[0] == "user" for a in group),
            "paid_ever_linked": sum(a in paid_ever for a in group),
            "preferred_game_types": dict(preferred),
            "september_returned": sum(any(row["day"] >= date(2026, 9, 1) for row in starts_by_actor[a]) for a in group),
        }
    active_weeks = {
        actor: len({(day.isocalendar().year, day.isocalendar().week) for day in days})
        for actor, days in august_active_days.items()
    }
    core = {
        "A_7_plus_active_days": sum(len(days) >= 7 for days in august_active_days.values()),
        "B_10_plus_completes": sum(completes_per_actor[actor] >= 10 for actor in august_player_actors),
        "C_20_plus_completes": sum(completes_per_actor[actor] >= 20 for actor in august_player_actors),
        "D_3_plus_active_weeks": sum(active_weeks[actor] >= 3 for actor in august_player_actors),
        "min_3_active_days": sum(len(days) >= 3 for days in august_active_days.values()),
        "min_7_active_days": sum(len(days) >= 7 for days in august_active_days.values()),
        "min_14_active_days": sum(len(days) >= 14 for days in august_active_days.values()),
        "min_20_active_days": sum(len(days) >= 20 for days in august_active_days.values()),
    }

    # Daily numeric summaries only over observed rows for each metric.
    daily_summary = {}
    start_summary_fields = {"unique_players", "game_start", "first_time_players", "returned_from_previous_day", "signup", "activated_player"}
    complete_summary_fields = {"game_complete", "completed_users"}
    for field in ("new_registrations", "unique_players", "game_start", "game_complete", "completed_users", "signup", "activated_player", "first_time_players", "returned_from_previous_day", "persisted_attempts"):
        eligible_rows = daily_rows
        if field in start_summary_fields:
            eligible_rows = [row for row in daily_rows if row["start_coverage"] == "observed_full_day"]
        elif field in complete_summary_fields:
            eligible_rows = [row for row in daily_rows if row["complete_coverage"] == "observed_full_day"]
        values = [float(row[field]) for row in eligible_rows if row[field] not in ("", None)]
        daily_summary[field] = {
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "average": round_or_none(mean(values)),
            "median": round_or_none(median(values)),
            "observed_days": len(values),
        }

    payments_summary = {
        "orders_created": len(august_tickets),
        "ticket_status": dict(Counter(row["status"] for row in august_tickets)),
        "provider_status": {"{}|{}".format(*key): value for key, value in provider_status.items()},
        "ticket_revenue_by_currency": {key: str(value) for key, value in ticket_revenue.items()},
        "ticket_revenue_by_provider_currency": {
            "{}|{}".format(*key): str(value) for key, value in ticket_revenue_by_provider.items()
        },
        "donations_created": len(august_donations_created),
        "donation_status_for_august_created": dict(Counter(row["status"] for row in august_donations_created)),
        "donations_confirmed": len(august_donations_confirmed),
        "donation_actual_revenue_by_currency": {key: str(value) for key, value in donation_revenue.items()},
        "tribute_purchase_webhooks": dict(Counter(
            row["status"] for row in tribute_purchases
            if AUG_START <= aware(row["purchase_created_at"] or row["received_at"]).astimezone(MSK) < AUG_END
        )),
        "tribute_intent_status": dict(Counter(row["status"] for row in tribute_intents)),
        "unique_linked_payers": len(august_payers),
        "first_time_linked_payers": first_time_payers,
        "repeat_linked_payers": repeat_payers,
        "linked_revenue_by_currency": {key: str(value) for key, value in linked_revenue.items()},
        "linked_revenue_per_payer_by_currency": {
            key: str((value / len(linked_payers_by_currency[key])).quantize(Decimal("0.01")))
            for key, value in linked_revenue.items() if linked_payers_by_currency[key]
        },
        "linked_player_to_payer_conversion_pct": round_or_none(
            pct(len(august_payers & august_player_actors), len(august_player_actors)), 2
        ),
        "unlinked_accepted_ticket_orders": sum(row["status"] == "Accepted" and not row["created_by_id"] for row in august_tickets),
    }

    summary = {
        "as_of_utc": as_of,
        "complete_observation_through_msk": complete_through,
        "period": {"start_msk": AUG_START, "end_exclusive_msk": AUG_END},
        "instrumentation": {
            "completion_table_applied_utc": "2026-08-10T19:20:23.531853+00:00",
            "start_table_applied_utc": "2026-08-15T13:49:44.449508+00:00",
            "salad_completion_code_commit_date": "2026-08-26",
            "onboarding_code_commit_date": "2026-08-26",
            "salad_nonpersisted_start_and_onboarding_delivery_fix_commit_date": "2026-08-28",
        },
        "audience": {
            "site_unique_users": None,
            "accounts_existing_at_august_end": sum(data["date_joined"] < AUG_END.astimezone(UTC) for data in users.values()),
            "new_registrations": len(auth_registrations),
            "observed_august_players": len(august_player_actors),
            "observed_registered_player_identities": sum(actor[0] == "user" for actor in august_player_actors),
            "observed_anonymous_player_identities": sum(actor[0] == "anon" for actor in august_player_actors),
            "observed_team_actors": sum(actor[0] == "team" for actor in august_player_actors),
            "new_observable_players": len(new_player_first),
            "game_starts": len(august_starts),
            "game_completes": len(august_completes),
            "persisted_attempts": sum(attempt_daily_counts.values()),
            "attempt_status_counts": attempt_status_counts,
            "unique_attempt_actors": len({actor for actor in attempt_actors if actor}),
            "players_with_completion": sum(completes_per_actor[actor] > 0 for actor in august_player_actors),
            "started_instances_completed_by_as_of": sum(
                row["instance_key"] in complete_by_instance for row in august_starts
            ),
            "started_instance_completion_pct": round_or_none(pct(
                sum(row["instance_key"] in complete_by_instance for row in august_starts),
                len(august_starts),
            ), 2),
            "avg_starts_per_player": round_or_none(mean(starts_per_actor[actor] for actor in august_player_actors)),
            "median_starts_per_player": round_or_none(median(starts_per_actor[actor] for actor in august_player_actors)),
            "avg_completes_per_player": round_or_none(mean(completes_per_actor[actor] for actor in august_player_actors)),
            "median_completes_per_player": round_or_none(median(completes_per_actor[actor] for actor in august_player_actors)),
            "completion_distribution": {segment: dict(counts) for segment, counts in completion_distribution.items()},
        },
        "daily_summary": daily_summary,
        "retention": retention_summary,
        "funnel_available_steps": dict(funnel_counts),
        "frequency": {
            "active_day_buckets": dict(active_day_buckets),
            "average_active_days": round_or_none(mean(len(days) for days in august_active_days.values())),
            "median_active_days": round_or_none(median(len(days) for days in august_active_days.values())),
            "average_interval_between_active_days": round_or_none(mean(all_gaps)),
            "max_consecutive_active_days": max(max_streak_by_actor.values()) if max_streak_by_actor else 0,
            "streak_distribution": {str(key): value for key, value in sorted(streak_distribution.items())},
            "core": core,
        },
        "first_game_types": dict(Counter(row["kind"] for row in new_player_first.values())),
        "first_three_sequences": dict(sequences.most_common(20)),
        "archive_vs_current": archive_summary,
        "current_completers_then_archive_same_day": {
            "users": len(current_then_archive_actors),
            "eligible_users": len(current_completer_actors),
            "pct": round_or_none(pct(len(current_then_archive_actors), len(current_completer_actors)), 2),
        },
        "first_complete_then_second_start_same_day": {
            "users": first_complete_then_second_same_day,
            "eligible_users": actors_with_aug_first_complete,
            "pct": round_or_none(pct(first_complete_then_second_same_day, actors_with_aug_first_complete), 2),
        },
        "registration": {
            "linked_new_registrations_with_completion_history": len(completions_before_signup),
            "median_completes_before_signup": round_or_none(median(completions_before_signup)),
            "mean_completes_before_signup": round_or_none(mean(completions_before_signup)),
            "completes_before_signup_distribution": dict(Counter(completions_before_signup)),
            "signup_conversion_by_august_completes": {
                label: {"registered": values[0], "players": values[1], "pct": round_or_none(pct(values[0], values[1]), 2)}
                for label, values in signup_conversion_buckets.items()
            },
        },
        "payments": payments_summary,
        "power_users": power_groups,
        "unavailable": {
            "landing_sessions_dau_and_site_users": "No server-side visit identity/event table; Yandex Metrika raw data is not stored in the production DB.",
            "onboarding_events": "Client-side Yandex Metrika goals only; no event rows/parameters in production DB.",
            "acquisition": "No UTM/referrer fields in server-side analytics tables.",
            "geo_language_device": "Not persisted in server-side analytics tables.",
            "sessions": "No analytics session id attached to starts/completions/attempts.",
        },
    }

    with (OUT_DIR / "audit_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, default=json_default)
        handle.write("\n")

    # Explicitly roll back the read-only transaction.  This is also a guard
    # against future accidental mutations added to this script.
    connection.rollback()
    print(json.dumps({
        "output_dir": str(OUT_DIR),
        "as_of_utc": as_of,
        "starts": len(august_starts),
        "completes": len(august_completes),
        "players": len(august_player_actors),
        "new_players": len(new_player_first),
        "new_registrations": len(auth_registrations),
    }, ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    try:
        main()
    finally:
        if connection.connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
