"""Dynamic, explainable difficulty for the three scheduled daily games.

The public value deliberately stays simple (1..5 stars).  The snapshot payload
keeps the observed metrics and historical baselines so an administrator can
audit every component without rerunning the aggregation.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import timedelta

from django.db.models import BooleanField, Case, F, Q, Value, When
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    DailyGameDifficulty,
    GameDifficultyNorm,
    GameTaskGroup,
    HiddenAnonKey,
    HintAttempt,
)


SUPPORTED_GAME_IDS = ('ladder', 'alphabetty', 'salad')
TASK_TYPE_BY_GAME_ID = {
    'ladder': 'raddle',
    'alphabetty': 'alphabetty',
    'salad': 'word_salad',
}

# All calibration constants live here intentionally.
MIN_DISPLAY_N = 5
PRELIMINARY_UNTIL_N = 10
MATURE_GAME_N = 30
MIN_MATURE_GAMES_FOR_NORM = 3
SHRINKAGE_PRIOR_N = 5
ACTION_GAP_CAP = timedelta(minutes=30)
UNFINISHED_WINDOW = timedelta(minutes=30)
DUE_REFRESH_LIMIT = 10
RATE_EPSILON = 0.01
NORM_METRICS = ('median_time', 'median_errors', 'help_rate', 'unfinished_rate')
# Cadence throttles dirty games; clean snapshots are not rebuilt just because time passed.
REFRESH_INTERVALS = (
    (timedelta(hours=6), timedelta(minutes=5)),
    (timedelta(hours=24), timedelta(minutes=15)),
    (timedelta(days=3), timedelta(hours=1)),
    (timedelta(days=7), timedelta(hours=6)),
    (timedelta(days=30), timedelta(hours=24)),
)
REFRESH_INTERVAL_OLD = timedelta(days=7)
REFRESH_CLAIM_LEASE = timedelta(minutes=5)
NORM_REFRESH_INTERVAL = timedelta(days=1)
RECENT_ENSURE_LOOKBACK_NUMBERS = 3
RECENT_ENSURE_AHEAD_NUMBERS = 7
REFRESH_RETRY_INTERVALS = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)
REFRESH_RETRY_INTERVAL_MAX = timedelta(hours=6)

TIME_RATIO_BOUNDS = (0.55, 0.80, 1.25, 1.80)
EFFORT_RATIO_BOUNDS = (0.50, 0.80, 1.25, 2.00)

BASE_WEIGHTS = {
    'ladder': {
        'time': 0.50,
        'errors': 0.20,
        'help': 0.15,
        'unfinished': 0.15,
    },
    'alphabetty': {
        'time': 0.45,
        'errors': 0.25,
        'help': 0.15,
        'unfinished': 0.15,
    },
    'salad': {
        'time': 0.65,
        # Wrong paths are not persisted by the current Salad UI.
        'help': 0.15,
        'unfinished': 0.20,
    },
}

STAR_LABELS = {
    1: 'Очень простая',
    2: 'Простая',
    3: 'Средняя',
    4: 'Сложная',
    5: 'Очень сложная',
}


def _actor_bucket(row):
    """Stable one-player key; authenticated identity wins over other fields."""
    if getattr(row, 'user_id', None):
        return ('user', row.user_id)
    if getattr(row, 'anon_key', None):
        return ('anon', str(row.anon_key))
    if getattr(row, 'team_id', None):
        return ('team', str(row.team_id))
    return None


def _actor_is_hidden(row, hidden_anons):
    team = getattr(row, 'team', None)
    if team is not None and (team.is_hidden or team.is_tester):
        return True
    anon_key = getattr(row, 'anon_key', None)
    return bool(anon_key and str(anon_key) in hidden_anons)


def _median(values):
    return float(statistics.median(values)) if values else None


def _safe_state(raw):
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _is_salad_hint(attempt):
    try:
        payload = json.loads(attempt.text or '{}')
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and (payload.get('action') or 'solve').strip().lower() == 'hint'


def _raddle_attempt_is_error(attempt, parsed):
    from games.raddle import word_matches

    if not parsed:
        return True
    try:
        payload = json.loads(attempt.text or '{}')
        word_index = int(payload.get('word_index', -1))
        word = str(payload.get('word') or '')
    except (TypeError, ValueError):
        return True
    if not 0 <= word_index < parsed['n_words']:
        return True
    return not word_matches(word, parsed['word_accept'][word_index])


def _active_seconds(attempts, completed_index):
    """Sum action gaps through completion, capping every gap at 30 minutes."""
    if completed_index is None:
        return None
    rows = attempts[:completed_index + 1]
    if len(rows) < 2:
        return 0.0
    cap = ACTION_GAP_CAP.total_seconds()
    total = 0.0
    for previous, current in zip(rows, rows[1:]):
        gap = max(0.0, (current.time - previous.time).total_seconds())
        total += min(gap, cap)
    return total


def _supported_tasks(placement):
    wanted = TASK_TYPE_BY_GAME_ID.get(placement.game_id)
    if not wanted:
        return []
    return list(
        placement.task_group.tasks.visible()
        .filter(task_type=wanted)
        .order_by('id')
    )


def calculate_observed_metrics(placement, *, now=None):
    """Aggregate one row per actor for a single daily-game edition."""
    now = now or timezone.now()
    game_id = placement.game_id
    tasks = _supported_tasks(placement)
    if game_id not in SUPPORTED_GAME_IDS or not tasks:
        return {
            'n': 0,
            'median_time': None,
            'median_errors': None,
            'help_rate': None,
            'unfinished_rate': None,
            'completed_n': 0,
            'unfinished_denominator': 0,
            'error_available': False,
        }

    task_ids = [task.pk for task in tasks]
    tasks_by_id = {task.pk: task for task in tasks}
    hidden_anons = set(HiddenAnonKey.objects.values_list('anon_key', flat=True))
    attempts_by_actor = defaultdict(list)

    attempts = (
        Attempt.manager.filter(task_id__in=task_ids, skip=False)
        .filter(Q(game=placement.game) | Q(game__isnull=True))
        .select_related('task', 'team', 'user')
        .order_by('time', 'pk')
    )
    for attempt in attempts.iterator():
        task = tasks_by_id.get(attempt.task_id)
        if task is None:
            continue
        # Null is retained for imported history; known stale revisions are not.
        if attempt.task_revision and attempt.task_revision != task.attempt_revision:
            continue
        actor = _actor_bucket(attempt)
        if actor is None or _actor_is_hidden(attempt, hidden_anons):
            continue
        attempts_by_actor[actor].append(attempt)

    help_actors = set()
    if game_id == 'ladder':
        hint_attempts = (
            HintAttempt.objects.filter(
                hint__task_id__in=task_ids,
                is_real_request=True,
            )
            .select_related('team', 'user')
        )
        for hint_attempt in hint_attempts.iterator():
            actor = _actor_bucket(hint_attempt)
            if actor is not None and not _actor_is_hidden(hint_attempt, hidden_anons):
                help_actors.add(actor)
    elif game_id == 'salad':
        for actor, actor_attempts in attempts_by_actor.items():
            if any(_is_salad_hint(attempt) for attempt in actor_attempts):
                help_actors.add(actor)
    elif game_id == 'alphabetty':
        states = (
            ChainTaskState.objects.filter(
                task_id__in=task_ids,
                game=placement.game,
                game_mode='general',
            )
            .select_related('team', 'user')
        )
        for state_row in states.iterator():
            actor = _actor_bucket(state_row)
            if actor is None or actor not in attempts_by_actor:
                continue
            if _actor_is_hidden(state_row, hidden_anons):
                continue
            state = _safe_state(state_row.state)
            if int(state.get('hints_taken') or 0) > 0:
                help_actors.add(actor)
        # Historical attempt states also cover rows predating ChainTaskState.
        for actor, actor_attempts in attempts_by_actor.items():
            if any(int(_safe_state(a.state).get('hints_taken') or 0) > 0 for a in actor_attempts):
                help_actors.add(actor)

    completed_times = []
    errors = []
    unfinished = 0
    unfinished_denominator = 0
    completed_n = 0
    parsed_raddles = {}
    if game_id == 'ladder':
        from games.raddle import parse_raddle_data
        parsed_raddles = {task.pk: parse_raddle_data(task) for task in tasks}

    for actor_attempts in attempts_by_actor.values():
        actor_attempts.sort(key=lambda attempt: (attempt.time, attempt.pk))
        completed_index = next(
            (index for index, attempt in enumerate(actor_attempts) if attempt.status == 'Ok'),
            None,
        )
        completed_at = None
        if completed_index is not None:
            completed_n += 1
            completed_at = actor_attempts[completed_index].time
            completed_times.append(_active_seconds(actor_attempts, completed_index))

        if game_id == 'ladder':
            errors.append(sum(
                _raddle_attempt_is_error(attempt, parsed_raddles.get(attempt.task_id))
                for attempt in actor_attempts[:completed_index + 1 if completed_index is not None else None]
            ))
        elif game_id == 'alphabetty':
            errors.append(sum(
                attempt.status != 'Ok'
                for attempt in actor_attempts[:completed_index + 1 if completed_index is not None else None]
            ))

        first_at = actor_attempts[0].time
        completed_in_window = bool(
            completed_at is not None
            and completed_at - first_at <= UNFINISHED_WINDOW
        )
        outcome_is_mature = completed_at is not None or now - first_at >= UNFINISHED_WINDOW
        if outcome_is_mature:
            unfinished_denominator += 1
            if not completed_in_window:
                unfinished += 1

    n = len(attempts_by_actor)
    error_available = game_id in ('ladder', 'alphabetty')
    return {
        'n': n,
        'median_time': _median(completed_times),
        'median_errors': _median(errors) if error_available else None,
        'help_rate': (len(help_actors & set(attempts_by_actor)) / n) if n else None,
        'unfinished_rate': (
            unfinished / unfinished_denominator
            if unfinished_denominator else None
        ),
        'completed_n': completed_n,
        'unfinished_denominator': unfinished_denominator,
        'error_available': error_available,
    }


def ratio_score(ratio, bounds):
    if ratio is None or not math.isfinite(float(ratio)):
        return None
    ratio = float(ratio)
    for score, upper in enumerate(bounds, start=1):
        if ratio < upper:
            return score
    return 5


def _ratio(value, typical, *, additive=0.0):
    if value is None or typical is None:
        return None
    numerator = float(value) + additive
    denominator = float(typical) + additive
    if denominator == 0:
        return 1.0 if numerator == 0 else None
    return numerator / denominator


def _norm_values_from_snapshots(game_id):
    mature = {metric: [] for metric in NORM_METRICS}
    available = {metric: [] for metric in NORM_METRICS}
    rows = DailyGameDifficulty.objects.filter(placement__game_id=game_id).only('n', 'payload')
    for row in rows.iterator():
        metrics = (row.payload or {}).get('metrics') or {}
        for metric in NORM_METRICS:
            value = metrics.get(metric)
            if value is None:
                continue
            number = float(value)
            if row.n >= MIN_DISPLAY_N:
                available[metric].append(number)
            if row.n >= MATURE_GAME_N:
                mature[metric].append(number)
    return mature, available


def refresh_historical_norm(game_id, *, now=None):
    """Rebuild the cached same-type baseline. Does not dirty existing snapshots."""
    now = now or timezone.now()
    mature, available = _norm_values_from_snapshots(game_id)
    typical = {}
    sources = {}
    for metric in NORM_METRICS:
        if len(mature[metric]) >= MIN_MATURE_GAMES_FOR_NORM:
            values, source = mature[metric], 'mature'
        elif available[metric]:
            values, source = available[metric], 'available'
        else:
            values, source = [], 'missing'
        typical[metric] = _median(values)
        sources[metric] = source
    payload = {**typical, 'sources': sources}
    row, _created = GameDifficultyNorm.objects.get_or_create(game_id=game_id)
    row.typical_time = typical['median_time']
    row.typical_errors = typical['median_errors']
    row.typical_help_rate = typical['help_rate']
    row.typical_unfinished_rate = typical['unfinished_rate']
    row.calculated_at = now
    row.payload = payload
    row.version = int(row.version or 0) + 1
    row.save(update_fields=[
        'typical_time', 'typical_errors', 'typical_help_rate',
        'typical_unfinished_rate', 'calculated_at', 'payload', 'version',
    ])
    return row


def refresh_stale_historical_norms(*, now=None, game_ids=None):
    now = now or timezone.now()
    stale_before = now - NORM_REFRESH_INTERVAL
    refreshed = []
    for game_id in tuple(game_ids or SUPPORTED_GAME_IDS):
        row = GameDifficultyNorm.objects.filter(game_id=game_id).first()
        if row is None or row.calculated_at is None or row.calculated_at <= stale_before:
            refresh_historical_norm(game_id, now=now)
            refreshed.append(game_id)
    return refreshed


def _norm_dict_from_row(row):
    sources = ((row.payload or {}).get('sources') or {}) if row else {}
    return {
        'median_time': row.typical_time if row else None,
        'median_errors': row.typical_errors if row else None,
        'help_rate': row.typical_help_rate if row else None,
        'unfinished_rate': row.typical_unfinished_rate if row else None,
        'sources': sources,
        'version': int(row.version or 0) if row else 0,
    }


def historical_norm(placement, metrics, *, now=None):
    """Return the cached type-wide baseline, creating it once if missing."""
    row = GameDifficultyNorm.objects.filter(game_id=placement.game_id).first()
    if row is None:
        row = refresh_historical_norm(placement.game_id, now=now)
    norm = _norm_dict_from_row(row)
    for metric in NORM_METRICS:
        if norm.get(metric) is None and metrics.get(metric) is not None:
            norm[metric] = float(metrics[metric])
            norm.setdefault('sources', {})[metric] = 'self_fallback'
    return norm


def rate_difficulty_metrics(game_id, metrics, norm):
    ratios = {
        'time': _ratio(metrics.get('median_time'), norm.get('median_time')),
        'errors': _ratio(
            metrics.get('median_errors'),
            norm.get('median_errors'),
            additive=1.0,
        ),
        # Absolute help rates differ dramatically by game type, so both rate
        # components use the same-type historical norm with a small epsilon.
        'help': _ratio(
            metrics.get('help_rate'),
            norm.get('help_rate'),
            additive=RATE_EPSILON,
        ),
        'unfinished': _ratio(
            metrics.get('unfinished_rate'),
            norm.get('unfinished_rate'),
            additive=RATE_EPSILON,
        ),
    }
    scores = {
        'time': ratio_score(ratios['time'], TIME_RATIO_BOUNDS),
        'errors': ratio_score(ratios['errors'], EFFORT_RATIO_BOUNDS),
        'help': ratio_score(ratios['help'], EFFORT_RATIO_BOUNDS),
        'unfinished': ratio_score(ratios['unfinished'], EFFORT_RATIO_BOUNDS),
    }
    configured = BASE_WEIGHTS[game_id]
    available_weights = {
        component: weight
        for component, weight in configured.items()
        if scores.get(component) is not None
    }
    total_weight = sum(available_weights.values())
    if total_weight:
        weights = {
            component: weight / total_weight
            for component, weight in available_weights.items()
        }
        raw_rating = sum(weights[component] * scores[component] for component in weights)
    else:
        weights = {}
        raw_rating = 3.0

    n = int(metrics.get('n') or 0)
    confidence = n / (n + SHRINKAGE_PRIOR_N) if n else 0.0
    adjusted = confidence * raw_rating + (1.0 - confidence) * 3.0
    adjusted = min(5.0, max(1.0, adjusted))
    stars = min(5, max(1, int(math.floor(adjusted + 0.5))))
    return {
        'ratios': ratios,
        'scores': scores,
        'weights': weights,
        'raw_rating': raw_rating,
        'confidence': confidence,
        'adjusted_rating': adjusted,
        'stars': stars,
        'is_visible': n >= MIN_DISPLAY_N,
        'is_preliminary': MIN_DISPLAY_N <= n < PRELIMINARY_UNTIL_N,
    }


def calculate_game_difficulty(placement, *, now=None, metrics=None, save=False):
    """Calculate a structured rating for one ``GameTaskGroup`` placement.

    ``save=True`` is for admin/manual rebuilds. Request handlers must not call
    this; they read ``DailyGameDifficulty`` via ``get_game_difficulty``.
    """
    if placement.game_id not in SUPPORTED_GAME_IDS:
        return None
    now = now or timezone.now()
    metrics = metrics or calculate_observed_metrics(placement, now=now)
    norm = historical_norm(placement, metrics, now=now)
    rating = rate_difficulty_metrics(placement.game_id, metrics, norm)
    result = {
        'game_id': placement.game_id,
        'placement_id': placement.pk,
        'number': placement.number,
        'n': metrics['n'],
        'metrics': metrics,
        'typical': norm,
        'norm_version': int(norm.get('version') or 0),
        **rating,
    }
    if save:
        persist_difficulty_snapshot(placement, result, now=now, force=True)
    return result


def _snapshot_write_defaults(placement, result, *, now, published_at=None, interval=None):
    published_at = published_at if published_at is not None else published_at_for_placement(placement)
    interval = interval or difficulty_refresh_interval(published_at, now)
    return {
        'n': result['n'],
        'payload': result,
        'stars': result['stars'] if result['is_visible'] else None,
        'is_preliminary': result['is_preliminary'],
        'calculated_at': now,
        'published_at': published_at,
        'norm_version': int(result.get('norm_version') or 0),
        'refresh_not_before': now + interval,
        'refresh_fail_count': 0,
        'refresh_last_error': '',
        'refresh_claim_token': None,
        'refresh_claimed_until': None,
    }


def persist_difficulty_snapshot(
    placement,
    result,
    *,
    now=None,
    claimed_revision=None,
    claim_token=None,
    force=False,
):
    """Write a calculated snapshot.

    Cron workers pass ``claim_token`` so a stale worker cannot overwrite a
    newer claim. ``dirty`` is cleared only when ``data_revision`` still equals
    the revision this worker calculated; a newer attempt during calculation
    keeps the row eligible.
    """
    now = now or timezone.now()
    ensure_daily_difficulty_row(placement, now=now)
    snapshot = DailyGameDifficulty.objects.filter(placement=placement).first()
    if snapshot is None:
        return 0
    if claimed_revision is None:
        claimed_revision = snapshot.data_revision
    defaults = _snapshot_write_defaults(
        placement,
        result,
        now=now,
        published_at=snapshot.published_at,
    )
    defaults['calculated_revision'] = claimed_revision
    defaults['dirty'] = Case(
        When(data_revision=claimed_revision, then=Value(False)),
        default=Value(True),
        output_field=BooleanField(),
    )
    filters = {'pk': snapshot.pk}
    if not force:
        if claim_token is None:
            return 0
        filters['refresh_claim_token'] = claim_token
    updated = DailyGameDifficulty.objects.filter(**filters).update(**defaults)
    if updated:
        result['snapshot_id'] = snapshot.pk
    return updated


def save_observed_metrics(placement, metrics, *, now=None):
    """First pass for bulk recalculation: persist facts before deriving norms."""
    now = now or timezone.now()
    snapshot = ensure_daily_difficulty_row(placement, now=now)
    payload = {
        'game_id': placement.game_id,
        'placement_id': placement.pk,
        'number': placement.number,
        'n': metrics['n'],
        'metrics': metrics,
    }
    DailyGameDifficulty.objects.filter(pk=snapshot.pk).update(
        n=metrics['n'],
        payload=payload,
        stars=None,
        is_preliminary=False,
        calculated_at=now,
        dirty=True,
        refresh_claim_token=None,
        refresh_claimed_until=None,
    )
    snapshot.n = metrics['n']
    snapshot.payload = payload
    return snapshot


def _supported_placements(*, game_ids=None):
    game_ids = tuple(game_ids or SUPPORTED_GAME_IDS)
    placements = list(
        GameTaskGroup.objects.filter(
            game_id__in=game_ids,
            task_group__tasks__task_type__in=[TASK_TYPE_BY_GAME_ID[g] for g in game_ids],
        )
        .select_related('game', 'task_group')
        .distinct()
    )
    placements.sort(key=lambda placement: (
        SUPPORTED_GAME_IDS.index(placement.game_id),
        placement.key_sort(),
    ))
    return placements


def published_at_for_placement(placement, *, now=None):
    from games.daily_section import publish_at_for
    return publish_at_for(placement.game, placement.number)


def difficulty_refresh_interval(published_at, now):
    """Maximum refresh frequency for a dirty game of this age.

    Clean games are not recalculated merely because this interval elapsed.
    """
    if published_at is None:
        return REFRESH_INTERVAL_OLD
    age = now - published_at
    if age < timedelta(0):
        age = timedelta(0)
    for max_age, interval in REFRESH_INTERVALS:
        if age < max_age:
            return interval
    return REFRESH_INTERVAL_OLD


def refresh_interval_for_age(age):
    """Back-compat wrapper around ``difficulty_refresh_interval``."""
    now = timezone.now()
    published_at = None if age is None else now - max(age, timedelta(0))
    return difficulty_refresh_interval(published_at, now)


def retry_delay_for_fail_count(fail_count):
    if fail_count <= 0:
        return timedelta(0)
    index = min(int(fail_count), len(REFRESH_RETRY_INTERVALS)) - 1
    if fail_count > len(REFRESH_RETRY_INTERVALS):
        return REFRESH_RETRY_INTERVAL_MAX
    return REFRESH_RETRY_INTERVALS[index]


def ensure_daily_difficulty_row(placement, *, now=None):
    if placement is None or placement.game_id not in SUPPORTED_GAME_IDS:
        return None
    now = now or timezone.now()
    published_at = published_at_for_placement(placement, now=now)
    snapshot, created = DailyGameDifficulty.objects.get_or_create(
        placement=placement,
        defaults={
            'data_revision': 1,
            'calculated_revision': 0,
            'dirty': True,
            'published_at': published_at,
            'refresh_not_before': published_at or now,
        },
    )
    if not created and published_at:
        updates = {}
        if snapshot.published_at != published_at:
            updates['published_at'] = published_at
        # Editions are created ahead of time. If the publication schedule is
        # later edited, the first-run deadline must follow it too. Previously
        # only published_at changed, leaving a newly published game asleep
        # until its old (sometimes weeks-later) date.
        if snapshot.calculated_at is None and snapshot.refresh_not_before != published_at:
            updates['refresh_not_before'] = published_at
        if updates:
            DailyGameDifficulty.objects.filter(pk=snapshot.pk).update(**updates)
            for field, value in updates.items():
                setattr(snapshot, field, value)
    return snapshot


def sync_daily_difficulty_schedule(game, *, now=None):
    """Synchronize every queue row immediately after a publish-start change."""
    if game is None or game.pk not in SUPPORTED_GAME_IDS:
        return {
            'placements': 0,
            'created': 0,
            'published_changed': 0,
            'first_deadline_changed': 0,
        }
    now = now or timezone.now()
    placements = list(
        game.task_group_links.select_related('game', 'task_group').all()
    )
    snapshots = {
        row.placement_id: row
        for row in DailyGameDifficulty.objects.filter(
            placement__game=game,
        )
    }
    report = {
        'placements': len(placements),
        'created': 0,
        'published_changed': 0,
        'first_deadline_changed': 0,
    }
    for placement in placements:
        before = snapshots.get(placement.pk)
        old_published_at = before.published_at if before else None
        old_deadline = before.refresh_not_before if before else None
        snapshot = ensure_daily_difficulty_row(placement, now=now)
        if before is None:
            report['created'] += 1
        if old_published_at != snapshot.published_at:
            report['published_changed'] += 1
        if snapshot.calculated_at is None and old_deadline != snapshot.refresh_not_before:
            report['first_deadline_changed'] += 1
    return report


def ensure_recent_difficulty_rows(*, now=None, game_ids=None):
    """Create missing rows for recently published (and a few upcoming) editions."""
    from games.daily_section import current_number_for
    from games.models import Game

    now = now or timezone.now()
    created = 0
    for game_id in tuple(game_ids or SUPPORTED_GAME_IDS):
        game = Game.objects.filter(pk=game_id).first()
        if game is None:
            continue
        current = current_number_for(game, now)
        if current is None:
            continue
        lo = max(1, int(current) - RECENT_ENSURE_LOOKBACK_NUMBERS)
        hi = int(current) + RECENT_ENSURE_AHEAD_NUMBERS
        numbers = [str(number) for number in range(lo, hi + 1)]
        placements = (
            GameTaskGroup.objects.filter(game_id=game_id, number__in=numbers)
            .select_related('game')
        )
        existing = set(
            DailyGameDifficulty.objects.filter(
                placement__game_id=game_id,
                placement__number__in=numbers,
            ).values_list('placement_id', flat=True)
        )
        for placement in placements:
            is_missing = placement.pk not in existing
            ensure_daily_difficulty_row(placement, now=now)
            if is_missing:
                created += 1
    return created


def backfill_daily_difficulty_rows(*, now=None, game_ids=None, dry_run=False):
    now = now or timezone.now()
    created = 0
    for placement in _supported_placements(game_ids=game_ids):
        exists = DailyGameDifficulty.objects.filter(placement=placement).exists()
        if exists:
            if not dry_run:
                ensure_daily_difficulty_row(placement, now=now)
            continue
        created += 1
        if not dry_run:
            ensure_daily_difficulty_row(placement, now=now)
    return created


def snapshot_is_due(snapshot, *, now=None):
    now = now or timezone.now()
    if snapshot is None or not snapshot.dirty:
        return False
    if snapshot.refresh_not_before is not None and snapshot.refresh_not_before > now:
        return False
    if snapshot.refresh_claimed_until is not None and snapshot.refresh_claimed_until > now:
        return False
    return True


def is_difficulty_refresh_due(placement, snapshot, *, now=None):
    """Scheduler eligibility for an existing snapshot. Missing rows are not due."""
    del placement
    return snapshot_is_due(snapshot, now=now)


def recalculate_all_daily_difficulties(*, game_ids=None, now=None, placement=None):
    """Two-pass rebuild so every rating sees the same observed history."""
    now = now or timezone.now()
    if placement is not None:
        placements = [placement]
        game_ids = (placement.game_id,)
    else:
        placements = _supported_placements(game_ids=game_ids)
    for row in placements:
        ensure_daily_difficulty_row(row, now=now)
    observed = {}
    for row in placements:
        metrics = calculate_observed_metrics(row, now=now)
        observed[row.pk] = metrics
        save_observed_metrics(row, metrics, now=now)
    for game_id in tuple(game_ids or SUPPORTED_GAME_IDS):
        refresh_historical_norm(game_id, now=now)
    results = []
    for row in placements:
        result = calculate_game_difficulty(
            row,
            now=now,
            metrics=observed[row.pk],
            save=True,
        )
        if result:
            results.append(result)
    return results


def _difficulty_queryset(*, task_id=None, game_id=None, task_group_id=None, placement_id=None):
    queryset = DailyGameDifficulty.objects.all()
    if placement_id:
        return queryset.filter(placement_id=placement_id)
    if game_id:
        if game_id not in SUPPORTED_GAME_IDS:
            return queryset.none()
        queryset = queryset.filter(placement__game_id=game_id)
    if task_group_id:
        queryset = queryset.filter(placement__task_group_id=task_group_id)
    elif task_id:
        queryset = queryset.filter(placement__task_group__tasks__id=task_id)
    return queryset


def mark_game_difficulty_changed(*, task_id=None, game_id=None, task_group_id=None, placement_id=None):
    """Atomically bump ``data_revision`` so a later cron tick can refresh."""
    queryset = _difficulty_queryset(
        task_id=task_id,
        game_id=game_id,
        task_group_id=task_group_id,
        placement_id=placement_id,
    )
    return queryset.update(
        data_revision=F('data_revision') + 1,
        dirty=True,
    )


def mark_daily_difficulty_dirty(*, task_id=None, game_id=None, task_group_id=None, placement_id=None):
    return mark_game_difficulty_changed(
        task_id=task_id,
        game_id=game_id,
        task_group_id=task_group_id,
        placement_id=placement_id,
    )


def _public_context(result):
    if not result or not result.get('is_visible'):
        return None
    stars = int(result['stars'])
    preliminary = bool(result['is_preliminary'])
    stars_text = '{}{}'.format('★' * stars, '☆' * (5 - stars))
    label = STAR_LABELS[stars]
    if preliminary:
        tooltip = None
        aria_label = 'Сложность: {} из 5 — {}. Предварительная оценка.'.format(
            stars, label.lower(),
        )
    else:
        tooltip = 'Сложность: {}.'.format(label)
        aria_label = 'Сложность: {} из 5 — {}.'.format(stars, label.lower())
    return {
        **result,
        'show': True,
        'stars_text': stars_text,
        'star_slots': [index < stars for index in range(5)],
        'label': label,
        'tooltip': tooltip,
        'aria_label': aria_label,
    }


def _snapshot_public_context(snapshot):
    """Build a public badge from a stored row, even if payload omitted visibility."""
    if snapshot is None:
        return None
    payload = dict(snapshot.payload or {})
    if payload.get('stars') is None and snapshot.stars is not None:
        payload['stars'] = snapshot.stars
    if payload.get('n') is None:
        payload['n'] = snapshot.n
    if payload.get('is_preliminary') is None:
        payload['is_preliminary'] = snapshot.is_preliminary
    if payload.get('is_visible') is None:
        payload['is_visible'] = bool(
            payload.get('stars') is not None
            and int(payload.get('n') or 0) >= MIN_DISPLAY_N
        )
    return _public_context(payload)


def get_cached_game_difficulties(placements):
    """Return public difficulty contexts for a list without recalculating them."""
    placements = list(placements)
    eligible = [
        placement
        for placement in placements
        if placement.game_id in SUPPORTED_GAME_IDS
    ]
    if not eligible:
        return {}
    snapshots = DailyGameDifficulty.objects.filter(
        placement_id__in=[placement.pk for placement in eligible],
    )
    result = {}
    for snapshot in snapshots:
        context = _snapshot_public_context(snapshot)
        if context is not None:
            result[snapshot.placement_id] = context
    return result


def get_game_difficulty(placement, *, force=False, now=None):
    """Read the stored public rating. Recalc only when ``force=True`` (admin)."""
    if placement is None or placement.game_id not in SUPPORTED_GAME_IDS:
        return None
    if force:
        result = calculate_game_difficulty(placement, now=now, save=True)
        return _public_context(result)
    snapshot = DailyGameDifficulty.objects.filter(placement=placement).first()
    return _snapshot_public_context(snapshot)


def refresh_due_daily_difficulties(*, now=None, limit=DUE_REFRESH_LIMIT, game_ids=None, dry_run=False):
    from games.difficulty_refresh import refresh_due_daily_difficulties as _refresh
    return _refresh(now=now, limit=limit, game_ids=game_ids, dry_run=dry_run)
