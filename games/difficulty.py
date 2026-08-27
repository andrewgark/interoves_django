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

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    DailyGameDifficulty,
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
DIRTY_REFRESH_INTERVAL = timedelta(minutes=5)
CACHE_LOCK_SECONDS = 30
RATE_EPSILON = 0.01

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
    1: 'очень легко',
    2: 'легко',
    3: 'средне',
    4: 'сложно',
    5: 'очень сложно',
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


def _historical_metric_values(game_id, metric, *, exclude_placement_id=None):
    rows = (
        DailyGameDifficulty.objects.filter(placement__game_id=game_id)
        .exclude(placement_id=exclude_placement_id)
        .only('n', 'payload')
    )
    mature = []
    available = []
    for row in rows:
        value = (row.payload or {}).get('metrics', {}).get(metric)
        if value is None:
            continue
        if row.n >= MIN_DISPLAY_N:
            available.append(float(value))
        if row.n >= MATURE_GAME_N:
            mature.append(float(value))
    if len(mature) >= MIN_MATURE_GAMES_FOR_NORM:
        return mature, 'mature'
    return available, 'available'


def historical_norm(placement, metrics):
    norm = {}
    sources = {}
    for metric in ('median_time', 'median_errors', 'help_rate', 'unfinished_rate'):
        values, source = _historical_metric_values(
            placement.game_id,
            metric,
            exclude_placement_id=placement.pk,
        )
        if not values and metrics.get(metric) is not None:
            values = [float(metrics[metric])]
            source = 'self_fallback'
        norm[metric] = _median(values)
        sources[metric] = source if values else 'missing'
    norm['sources'] = sources
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
    """Calculate a structured rating for one ``GameTaskGroup`` placement."""
    if placement.game_id not in SUPPORTED_GAME_IDS:
        return None
    metrics = metrics or calculate_observed_metrics(placement, now=now)
    norm = historical_norm(placement, metrics)
    rating = rate_difficulty_metrics(placement.game_id, metrics, norm)
    result = {
        'game_id': placement.game_id,
        'placement_id': placement.pk,
        'number': placement.number,
        'n': metrics['n'],
        'metrics': metrics,
        'typical': norm,
        **rating,
    }
    if save:
        snapshot, _ = DailyGameDifficulty.objects.update_or_create(
            placement=placement,
            defaults={
                'n': result['n'],
                'payload': result,
                'stars': result['stars'] if result['is_visible'] else None,
                'is_preliminary': result['is_preliminary'],
                'dirty': False,
                'calculated_at': now or timezone.now(),
            },
        )
        result['snapshot_id'] = snapshot.pk
    return result


def save_observed_metrics(placement, metrics, *, now=None):
    """First pass for bulk recalculation: persist facts before deriving norms."""
    snapshot, _ = DailyGameDifficulty.objects.update_or_create(
        placement=placement,
        defaults={
            'n': metrics['n'],
            'payload': {
                'game_id': placement.game_id,
                'placement_id': placement.pk,
                'number': placement.number,
                'n': metrics['n'],
                'metrics': metrics,
            },
            'stars': None,
            'is_preliminary': False,
            'dirty': True,
            'calculated_at': now or timezone.now(),
        },
    )
    return snapshot


def recalculate_all_daily_difficulties(*, game_ids=None, now=None):
    """Two-pass rebuild so every rating sees the same observed history."""
    now = now or timezone.now()
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
    observed = {}
    for placement in placements:
        metrics = calculate_observed_metrics(placement, now=now)
        observed[placement.pk] = metrics
        save_observed_metrics(placement, metrics, now=now)
    return [
        calculate_game_difficulty(
            placement,
            now=now,
            metrics=observed[placement.pk],
            save=True,
        )
        for placement in placements
    ]


def mark_daily_difficulty_dirty(*, task_id=None, game_id=None, task_group_id=None):
    if game_id and game_id not in SUPPORTED_GAME_IDS:
        return 0
    queryset = DailyGameDifficulty.objects.all()
    if game_id:
        queryset = queryset.filter(placement__game_id=game_id)
    if task_group_id:
        queryset = queryset.filter(placement__task_group_id=task_group_id)
    elif task_id:
        queryset = queryset.filter(placement__task_group__tasks__id=task_id)
    return queryset.update(dirty=True)


def _public_context(result):
    if not result or not result.get('is_visible'):
        return None
    stars = int(result['stars'])
    n = int(result['n'])
    preliminary = bool(result['is_preliminary'])
    stars_text = '{}{}'.format('★' * stars, '☆' * (5 - stars))
    if preliminary:
        tooltip = 'Сложность рассчитана по результатам {} игроков и может измениться.'.format(n)
    else:
        tooltip = 'Сложность рассчитана по результатам {} игроков.'.format(n)
    return {
        **result,
        'show': True,
        'stars_text': stars_text,
        'label': STAR_LABELS[stars],
        'tooltip': tooltip,
    }


def get_game_difficulty(placement, *, force=False, now=None):
    """Cheap page-read path backed by a persistent, throttled snapshot."""
    if placement is None or placement.game_id not in SUPPORTED_GAME_IDS:
        return None
    now = now or timezone.now()
    snapshot = DailyGameDifficulty.objects.filter(placement=placement).first()
    needs_refresh = force or snapshot is None
    if snapshot is not None and snapshot.dirty:
        needs_refresh = bool(
            snapshot.calculated_at is None
            or now - snapshot.calculated_at >= DIRTY_REFRESH_INTERVAL
        )
    if needs_refresh:
        lock_key = 'daily-difficulty:refresh:{}'.format(placement.pk)
        locked = cache.add(lock_key, 1, CACHE_LOCK_SECONDS)
        if locked or snapshot is None:
            try:
                result = calculate_game_difficulty(placement, now=now, save=True)
                return _public_context(result)
            finally:
                if locked:
                    cache.delete(lock_key)
    if snapshot is None:
        return None
    return _public_context(snapshot.payload or {})
