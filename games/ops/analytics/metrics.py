"""Canonical Stage 2A product metrics over post-cutover backend rows.

Trusted identity boundary is the Stage 1C cutover (SHA 6c53989). Phase E
signed-cookie enforcement does not move this timestamp.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Count, Exists, Min, OuterRef, Q
from django.utils import timezone

from games.models import PlayerCompletedGame, PlayerStartedGame

MOSCOW = ZoneInfo('Europe/Moscow')
TRUSTED_IDENTITY_CUTOVER_SHA = '6c53989'
TRUSTED_IDENTITY_CUTOVER = datetime(2026, 9, 13, 21, 24, 38, tzinfo=timezone.utc)
ROLLING_WAU_DAYS = 7
ROLLING_MAU_DAYS = 30
CORE_WINDOW_DAYS = 30


def trusted_identity_cutover() -> datetime:
    """Stage 1C identity cutover. Phase E does not change this value."""
    return TRUSTED_IDENTITY_CUTOVER


def _valid_identity_q() -> Q:
    return (
        Q(user_id__isnull=False, team_id__isnull=True, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=False, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=True, anon_key__isnull=False)
    )


def _eligible_starts():
    return PlayerStartedGame.objects.filter(_valid_identity_q(), is_backfilled=False)


def _eligible_completions():
    return PlayerCompletedGame.objects.filter(_valid_identity_q(), is_backfilled=False)


def _apply_filters(qs, *, game_kind=None, placement=None):
    if game_kind:
        qs = qs.filter(game_kind=game_kind)
    if placement:
        qs = qs.filter(game_instance_id=placement)
    return qs


def _moscow_date(value) -> date:
    if timezone.is_naive(value):
        value = timezone.make_aware(value, MOSCOW)
    return value.astimezone(MOSCOW).date()


def _moscow_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=MOSCOW)


def _actor_slices(queryset):
    return (
        (
            'user',
            queryset.filter(
                user_id__isnull=False,
                team_id__isnull=True,
                anon_key__isnull=True,
            ),
            {
                'user_id': OuterRef('user_id'),
                'team_id__isnull': True,
                'anon_key__isnull': True,
            },
        ),
        (
            'team',
            queryset.filter(
                user_id__isnull=True,
                team_id__isnull=False,
                anon_key__isnull=True,
            ),
            {
                'team_id': OuterRef('team_id'),
                'user_id__isnull': True,
                'anon_key__isnull': True,
            },
        ),
        (
            'anonymous',
            queryset.filter(
                user_id__isnull=True,
                team_id__isnull=True,
                anon_key__isnull=False,
            ),
            {
                'anon_key': OuterRef('anon_key'),
                'user_id__isnull': True,
                'team_id__isnull': True,
            },
        ),
    )


def _namespace_counts(qs) -> tuple[int, int, int, int]:
    row = qs.aggregate(
        registered=Count('user_id', filter=Q(user_id__isnull=False), distinct=True),
        anonymous=Count(
            'anon_key',
            filter=Q(anon_key__isnull=False) & ~Q(anon_key=''),
            distinct=True,
        ),
        team=Count('team_id', filter=Q(team_id__isnull=False), distinct=True),
    )
    registered = row['registered'] or 0
    anonymous = row['anonymous'] or 0
    team = row['team'] or 0
    return registered, anonymous, team, registered + anonymous + team


def _first_trusted_starts(until: datetime, *, game_kind=None, placement=None) -> dict:
    qs = _apply_filters(
        _eligible_starts().filter(
            started_at__gte=TRUSTED_IDENTITY_CUTOVER,
            started_at__lt=until,
        ),
        game_kind=game_kind,
        placement=placement,
    )
    first = {}
    for user_id, first_at in (
        qs.filter(user_id__isnull=False)
        .values('user_id')
        .annotate(first_at=Min('started_at'))
        .values_list('user_id', 'first_at')
    ):
        first[('user', user_id)] = first_at
    for anon_key, first_at in (
        qs.filter(anon_key__isnull=False)
        .values('anon_key')
        .annotate(first_at=Min('started_at'))
        .values_list('anon_key', 'first_at')
    ):
        first[('anon', anon_key)] = first_at
    for team_id, first_at in (
        qs.filter(team_id__isnull=False)
        .values('team_id')
        .annotate(first_at=Min('started_at'))
        .values_list('team_id', 'first_at')
    ):
        first[('team', team_id)] = first_at
    return first


def _actor_from_row(user_id, anon_key, team_id):
    if user_id is not None:
        return ('user', user_id)
    if anon_key:
        return ('anon', anon_key)
    if team_id is not None:
        return ('team', team_id)
    return None


def _activity_days(qs) -> dict[tuple, set[date]]:
    days: dict[tuple, set[date]] = {}
    for user_id, anon_key, team_id, started_at in qs.values_list(
        'user_id', 'anon_key', 'team_id', 'started_at',
    ):
        actor = _actor_from_row(user_id, anon_key, team_id)
        if actor is None:
            continue
        days.setdefault(actor, set()).add(_moscow_date(started_at))
    return days


def _complete_days(since: datetime, until: datetime) -> list[date]:
    start_day = _moscow_date(since)
    last_instant = until - timedelta(microseconds=1)
    end_day = _moscow_date(last_instant)
    days = []
    day = start_day
    while day <= end_day:
        start = _moscow_midnight(day)
        end = start + timedelta(days=1)
        if since <= start and until >= end:
            days.append(day)
        day += timedelta(days=1)
    return days


def _rate_payload(numerator: int, denominator: int, *, observable: bool) -> dict:
    if not observable:
        return {'value': None, 'status': 'not_yet_observable', 'numerator': None, 'denominator': denominator}
    if denominator == 0:
        return {'value': None, 'status': 'empty_cohort', 'numerator': 0, 'denominator': 0}
    value = float(Decimal(numerator) / Decimal(denominator))
    return {
        'value': value,
        'status': 'ok',
        'numerator': numerator,
        'denominator': denominator,
    }


def _day_elapsed(day: date, until: datetime) -> bool:
    return until >= _moscow_midnight(day) + timedelta(days=1)


def _completion_rate(window_starts) -> dict:
    started = window_starts.count()
    completed = 0
    for _label, actor_starts, actor_lookup in _actor_slices(window_starts):
        matching = _eligible_completions().filter(
            game_instance_id=OuterRef('game_instance_id'),
            **actor_lookup,
        )
        completed += actor_starts.annotate(has_complete=Exists(matching)).filter(
            has_complete=True,
        ).count()
    if started == 0:
        return {'value': None, 'status': 'empty', 'started_placements': 0, 'completed_placements': 0}
    return {
        'value': float(Decimal(completed) / Decimal(started)),
        'status': 'ok',
        'started_placements': started,
        'completed_placements': completed,
    }


def _active_day_distribution(period_days: dict[tuple, set[date]]) -> dict[str, int]:
    counts: dict[int, int] = {}
    for days in period_days.values():
        n = len(days)
        counts[n] = counts.get(n, 0) + 1
    return {str(k): counts[k] for k in sorted(counts)}


def _core_player_counts(until: datetime, *, game_kind=None, placement=None) -> dict:
    window_start = until - timedelta(days=CORE_WINDOW_DAYS)
    if window_start < TRUSTED_IDENTITY_CUTOVER:
        window_start = TRUSTED_IDENTITY_CUTOVER
    starts = _apply_filters(
        _eligible_starts().filter(started_at__gte=window_start, started_at__lt=until),
        game_kind=game_kind,
        placement=placement,
    )
    completions = _apply_filters(
        _eligible_completions().filter(completed_at__gte=window_start, completed_at__lt=until),
        game_kind=game_kind,
        placement=placement,
    )
    start_days = _activity_days(starts)
    completion_counts: dict[tuple, int] = {}
    for user_id, anon_key, team_id in completions.values_list('user_id', 'anon_key', 'team_id'):
        actor = _actor_from_row(user_id, anon_key, team_id)
        if actor is None:
            continue
        completion_counts[actor] = completion_counts.get(actor, 0) + 1

    active_7 = 0
    weeks_3 = 0
    for days in start_days.values():
        if len(days) >= 7:
            active_7 += 1
        weeks = {(day.isocalendar().year, day.isocalendar().week) for day in days}
        if len(weeks) >= 3:
            weeks_3 += 1
    complete_10 = sum(1 for n in completion_counts.values() if n >= 10)
    complete_20 = sum(1 for n in completion_counts.values() if n >= 20)
    return {
        'window_days': CORE_WINDOW_DAYS,
        'window_start': window_start.isoformat(),
        'active_days_7_plus': active_7,
        'completions_10_plus': complete_10,
        'completions_20_plus': complete_20,
        'active_weeks_3_plus': weeks_3,
    }


def _retention_rows(
    first_starts: dict,
    activity_days: dict[tuple, set[date]],
    since: datetime,
    until: datetime,
) -> list[dict]:
    cohorts: dict[date, list[tuple]] = {}
    for actor, first_at in first_starts.items():
        if first_at < since or first_at >= until:
            continue
        cohorts.setdefault(_moscow_date(first_at), []).append(actor)

    rows = []
    for day in sorted(cohorts):
        members = cohorts[day]
        size = len(members)
        d1_day = day + timedelta(days=1)
        d7_day = day + timedelta(days=7)
        exact_d1_obs = _day_elapsed(d1_day, until)
        exact_d7_obs = _day_elapsed(d7_day, until)
        rolling_obs = exact_d7_obs
        exact_d1 = sum(
            1 for actor in members if d1_day in activity_days.get(actor, set())
        )
        exact_d7 = sum(
            1 for actor in members if d7_day in activity_days.get(actor, set())
        )
        rolling_d7 = sum(
            1
            for actor in members
            if any(active >= d7_day for active in activity_days.get(actor, set()))
        )
        rows.append({
            'cohort_date': day.isoformat(),
            'cohort_size': size,
            'exact_d1': _rate_payload(exact_d1, size, observable=exact_d1_obs),
            'exact_d7': _rate_payload(exact_d7, size, observable=exact_d7_obs),
            'rolling_d7': _rate_payload(rolling_d7, size, observable=rolling_obs),
        })
    return rows


def _iso(value: datetime) -> str:
    return value.isoformat()


@dataclass
class ProductMetricsReport:
    since: str
    until: str
    timezone: str
    trusted_cutover: str
    trusted_cutover_sha: str
    legacy_contaminated: bool
    game_kind: str | None
    placement: str | None
    overview: dict = field(default_factory=dict)
    engagement: dict = field(default_factory=dict)
    retention: list = field(default_factory=list)
    core: dict = field(default_factory=dict)
    by_game_kind: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_product_metrics_report(
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    game_kind: str | None = None,
    placement: str | None = None,
) -> ProductMetricsReport:
    """Build the canonical Stage 2A report for ``[since, until)``."""
    until = until or timezone.now()
    since = since or TRUSTED_IDENTITY_CUTOVER
    if timezone.is_naive(since):
        since = timezone.make_aware(since, MOSCOW)
    if timezone.is_naive(until):
        until = timezone.make_aware(until, MOSCOW)
    if until <= since:
        raise ValueError('until must be after since')

    legacy = since < TRUSTED_IDENTITY_CUTOVER
    period_starts = _apply_filters(
        _eligible_starts().filter(started_at__gte=since, started_at__lt=until),
        game_kind=game_kind,
        placement=placement,
    )
    period_completions = _apply_filters(
        _eligible_completions().filter(completed_at__gte=since, completed_at__lt=until),
        game_kind=game_kind,
        placement=placement,
    )
    registered, anonymous, team, players = _namespace_counts(period_starts)
    starts_n = period_starts.count()
    completions_n = period_completions.count()

    first_starts = _first_trusted_starts(until, game_kind=game_kind, placement=placement)
    new_players = sum(1 for first_at in first_starts.values() if since <= first_at < until)

    trusted_activity_qs = _apply_filters(
        _eligible_starts().filter(
            started_at__gte=TRUSTED_IDENTITY_CUTOVER,
            started_at__lt=until,
        ),
        game_kind=game_kind,
        placement=placement,
    )
    activity_days = _activity_days(trusted_activity_qs)
    period_days = _activity_days(period_starts)

    complete_days = _complete_days(since, until)
    dau_values = []
    for day in complete_days:
        actors = {actor for actor, days in period_days.items() if day in days}
        dau_values.append(len(actors))
    dau_average = (
        float(Decimal(sum(dau_values)) / Decimal(len(dau_values))) if dau_values else None
    )

    wau_since = until - timedelta(days=ROLLING_WAU_DAYS)
    mau_since = until - timedelta(days=ROLLING_MAU_DAYS)
    if not legacy:
        wau_since = max(wau_since, TRUSTED_IDENTITY_CUTOVER)
        mau_since = max(mau_since, TRUSTED_IDENTITY_CUTOVER)
    wau_since = max(wau_since, since) if legacy else wau_since
    mau_since = max(mau_since, since) if legacy else mau_since
    wau = _namespace_counts(
        _apply_filters(
            _eligible_starts().filter(started_at__gte=wau_since, started_at__lt=until),
            game_kind=game_kind,
            placement=placement,
        )
    )[3]
    mau = _namespace_counts(
        _apply_filters(
            _eligible_starts().filter(started_at__gte=mau_since, started_at__lt=until),
            game_kind=game_kind,
            placement=placement,
        )
    )[3]

    kind_rows = [
        {'game_kind': row['game_kind'], 'starts': row['starts']}
        for row in (
            period_starts.values('game_kind')
            .annotate(starts=Count('id'))
            .order_by('game_kind')
        )
    ]

    return ProductMetricsReport(
        since=_iso(since),
        until=_iso(until),
        timezone='Europe/Moscow',
        trusted_cutover=_iso(TRUSTED_IDENTITY_CUTOVER),
        trusted_cutover_sha=TRUSTED_IDENTITY_CUTOVER_SHA,
        legacy_contaminated=legacy,
        game_kind=game_kind,
        placement=placement,
        overview={
            'players': players,
            'new_players': new_players,
            'starts': starts_n,
            'completions': completions_n,
            'completion_rate': _completion_rate(period_starts),
            'registered_players': registered,
            'anonymous_players': anonymous,
            'team_players': team,
        },
        engagement={
            'dau_average': dau_average,
            'dau_complete_days': len(complete_days),
            'wau': wau,
            'mau': mau,
            'wau_window_start': _iso(wau_since),
            'mau_window_start': _iso(mau_since),
            'active_days_distribution': _active_day_distribution(period_days),
        },
        retention=_retention_rows(first_starts, activity_days, since, until),
        core=_core_player_counts(until, game_kind=game_kind, placement=placement),
        by_game_kind=kind_rows,
    )
