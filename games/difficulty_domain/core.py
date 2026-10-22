"""Dynamic, explainable difficulty for the scheduled daily games.

The public value deliberately stays simple (1..5 stars).  The snapshot payload
keeps the observed metrics and historical baselines so an administrator can
audit every component without rerunning the aggregation.

Each component ratio blends two views of the same edition:

* classic — the edition's aggregate against the game type's historical norm;
* paired — every returning player against their own previous editions, so a
  cohort of strong early players does not make a hard edition look easy.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import BooleanField, Case, F, Q, Value, When
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    DailyDifficultyActorResult,
    DailyGameDifficulty,
    GameDifficultyNorm,
    GameTaskGroup,
    HiddenAnonKey,
    HintAttempt,
    RandomAlphabettyGame,
    RandomCensorlyGame,
)


SUPPORTED_GAME_IDS = ('ladder', 'alphabetty', 'salad', 'censorly')
TASK_TYPE_BY_GAME_ID = {
    'ladder': 'raddle',
    'alphabetty': 'alphabetty',
    'salad': 'word_salad',
    'censorly': 'censorly',
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
NORM_METRICS = ('median_time', 'median_attempts', 'median_errors', 'help_rate', 'unfinished_rate')
# Cadence throttles dirty games; clean snapshots are not rebuilt just because time passed.
# Age counts from publication or from the last task edit, whichever is newer.
REFRESH_INTERVALS = (
    (timedelta(hours=1), timedelta(minutes=1)),
    (timedelta(hours=6), timedelta(minutes=10)),
    (timedelta(hours=24), timedelta(minutes=30)),
    (timedelta(days=3), timedelta(hours=2)),
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

# Within-player comparison: a player needs this many earlier editions of the
# same game to contribute, and only their most recent window is used so that a
# player's improvement over months does not distort the baseline.
PAIRED_MIN_HISTORY = 3
PAIRED_HISTORY_WINDOW = 30
# Blend weight of the paired ratio is paired_n / (paired_n + PAIRED_PRIOR_N).
PAIRED_PRIOR_N = 10
PAIRED_ACTOR_CHUNK = 500
PAIRED_COMPONENTS = ('time', 'attempts', 'errors', 'help', 'unfinished')

BASE_WEIGHTS = {
    'ladder': {
        'time': 0.45,
        'errors': 0.20,
        'help': 0.15,
        'unfinished': 0.20,
    },
    'alphabetty': {
        'time': 0.40,
        'errors': 0.25,
        'help': 0.15,
        'unfinished': 0.20,
    },
    'salad': {
        'time': 0.55,
        # Wrong paths are not persisted by the current Salad UI.
        'help': 0.15,
        'unfinished': 0.30,
    },
    'censorly': {
        'time': 0.25,
        'attempts': 0.30,
        'errors': 0.15,
        'help': 0.10,
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


def _is_random_alphabetty_placement(placement) -> bool:
    """Random alphabetty games are permanent, not scheduled daily editions."""
    return bool(
        placement is not None
        and placement.game_id == 'alphabetty'
        and RandomAlphabettyGame.objects.filter(
            task_group_id=placement.task_group_id,
        ).exists()
    )


def _is_random_censorly_placement(placement) -> bool:
    return bool(
        placement is not None
        and placement.game_id == 'censorly'
        and RandomCensorlyGame.objects.filter(
            task_group_id=placement.task_group_id,
        ).exists()
    )


def _is_random_daily_placement(placement) -> bool:
    return _is_random_alphabetty_placement(placement) or _is_random_censorly_placement(placement)


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


def _played_seconds(attempts, now):
    """Active time of a player who is still playing, including the open gap."""
    since_last = max(0.0, (now - attempts[-1].time).total_seconds())
    return _active_seconds(attempts, len(attempts) - 1) + min(since_last, ACTION_GAP_CAP.total_seconds())


def _single_revision_attempts(task_attempts):
    """One player's attempts on one task, restricted to a single task revision.

    Editing a published task rotates ``attempt_revision``. Dropping every
    older attempt used to erase most of an edition's players and leave the
    rest with a single attempt (zero active time). A player is judged on the
    revision they solved first, otherwise on the one they played last. Null
    revisions are imported history and belong to any revision.
    """
    revisions = {attempt.task_revision for attempt in task_attempts if attempt.task_revision}
    if len(revisions) <= 1:
        return task_attempts
    anchor = next(
        (attempt for attempt in task_attempts if attempt.status == 'Ok' and attempt.task_revision),
        None,
    ) or next(attempt for attempt in reversed(task_attempts) if attempt.task_revision)
    return [
        attempt for attempt in task_attempts
        if not attempt.task_revision or attempt.task_revision == anchor.task_revision
    ]


def _attempts_of_played_revision(attempts):
    by_task = defaultdict(list)
    for attempt in attempts:
        by_task[attempt.task_id].append(attempt)
    kept = []
    for task_attempts in by_task.values():
        kept.extend(_single_revision_attempts(task_attempts))
    kept.sort(key=lambda attempt: (attempt.time, attempt.pk))
    return kept


def _censored_median(observations):
    """Kaplan-Meier median of ``(value, solved)`` observations.

    An unsolved observation is right-censored: that player needs more than
    ``value``. Abandoned players carry ``math.inf`` and therefore never solve.
    Returns ``(median, reached)``. When at most half of the players solved,
    ``median`` is the largest finite observation, a lower bound of the truth.
    """
    if not observations:
        return None, False
    if all(solved for _value, solved in observations):
        return _median([value for value, _solved in observations]), True
    at_risk = len(observations)
    survival = 1.0
    # Solves sort before censorings at the same value, as in standard KM.
    for value, solved in sorted(observations, key=lambda item: (item[0], not item[1])):
        if solved:
            survival *= (at_risk - 1) / at_risk
            if survival <= 0.5:
                return float(value), True
        at_risk -= 1
    finite = [value for value, _solved in observations if math.isfinite(value)]
    return (float(max(finite)) if finite else None), False


def _censored_ratio(ratio):
    """A lower-bound ratio proves only "slower than usual"; below 1 it says nothing."""
    return ratio if ratio is not None and ratio >= 1.0 else None


def _supported_tasks(placement):
    wanted = TASK_TYPE_BY_GAME_ID.get(placement.game_id)
    if not wanted:
        return []
    return list(
        placement.task_group.tasks.visible()
        .filter(task_type=wanted)
        .order_by('id')
    )


def observe_actor_results(placement, *, now=None):
    """One outcome row per visible player of a daily edition.

    Returns ``None`` for unsupported placements. Rows are persisted as
    ``DailyDifficultyActorResult`` and feed the within-player comparison.
    """
    now = now or timezone.now()
    game_id = placement.game_id
    tasks = _supported_tasks(placement)
    if game_id not in SUPPORTED_GAME_IDS or not tasks:
        return None

    task_ids = [task.pk for task in tasks]
    hidden_anons = set(HiddenAnonKey.objects.values_list('anon_key', flat=True))
    attempts_by_actor = defaultdict(list)

    attempts = (
        Attempt.manager.filter(task_id__in=task_ids, skip=False, replay_slot__isnull=True)
        .filter(Q(game=placement.game) | Q(game__isnull=True))
        .select_related('team')
        .order_by('time', 'pk')
    )
    if game_id == 'censorly':
        # Censorly attempts store a growing puzzle-state JSON blob. Loading it
        # for every historical guess can exhaust the 1 GiB worker even though
        # difficulty only needs the guess text, status, timestamp, and actor.
        attempts = attempts.defer('state', 'comment')
    for attempt in attempts.iterator():
        actor = _actor_bucket(attempt)
        if actor is None or _actor_is_hidden(attempt, hidden_anons):
            continue
        attempts_by_actor[actor].append(attempt)
    for actor, actor_attempts in attempts_by_actor.items():
        attempts_by_actor[actor] = _attempts_of_played_revision(actor_attempts)

    help_actors = set()
    if game_id == 'ladder':
        hint_attempts = (
            HintAttempt.objects.filter(
                hint__task_id__in=task_ids,
                is_real_request=True,
                replay_slot__isnull=True,
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
                replay_slot__isnull=True,
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
    elif game_id == 'censorly':
        for actor, actor_attempts in attempts_by_actor.items():
            if any(str(attempt.text or '').startswith('#hint:') for attempt in actor_attempts):
                help_actors.add(actor)

    parsed_raddles = {}
    if game_id == 'ladder':
        from games.raddle import parse_raddle_data
        parsed_raddles = {task.pk: parse_raddle_data(task) for task in tasks}

    rows = []
    for actor, actor_attempts in attempts_by_actor.items():
        completed_index = next(
            (index for index, attempt in enumerate(actor_attempts) if attempt.status == 'Ok'),
            None,
        )
        completed = completed_index is not None
        observed = actor_attempts[:completed_index + 1] if completed else actor_attempts
        word_attempts = None
        errors = None
        if game_id == 'ladder':
            errors = sum(
                _raddle_attempt_is_error(attempt, parsed_raddles.get(attempt.task_id))
                for attempt in observed
            )
        elif game_id == 'alphabetty':
            errors = sum(attempt.status != 'Ok' for attempt in observed)
        elif game_id == 'censorly':
            guesses = [attempt for attempt in observed if not str(attempt.text or '').startswith('#hint:')]
            word_attempts = len(guesses)
            errors = sum(attempt.status == 'Wrong' for attempt in guesses)

        first_at = actor_attempts[0].time
        completed_at = actor_attempts[completed_index].time if completed else None
        abandoned = not completed and now - actor_attempts[-1].time >= UNFINISHED_WINDOW
        if completed:
            time_seconds = _active_seconds(actor_attempts, completed_index)
        elif abandoned:
            time_seconds = _active_seconds(actor_attempts, len(actor_attempts) - 1)
        else:
            time_seconds = _played_seconds(actor_attempts, now)
        # The outcome is known only for players who started a full window ago.
        # Counting early solvers sooner made a fresh edition look unfinished-free.
        unfinished = None
        if now - first_at >= UNFINISHED_WINDOW:
            unfinished = not (completed and completed_at - first_at <= UNFINISHED_WINDOW)
        rows.append({
            'actor': '{}:{}'.format(*actor),
            'completed': completed,
            'abandoned': abandoned,
            'time_seconds': time_seconds,
            'attempts': word_attempts,
            'errors': errors,
            'used_help': actor in help_actors,
            'unfinished': unfinished,
        })
    return rows


def aggregate_actor_results(game_id, rows):
    """Edition-level metrics from ``observe_actor_results`` rows.

    Time is a censored median: players still on the puzzle count as "slower
    than they are so far" and abandoned players as never solving, so a hard
    edition is visible before most of its players reach the finish line.
    """
    error_available = rows is not None and game_id in ('ladder', 'alphabetty', 'censorly')
    rows = rows or []
    n = len(rows)
    time_observations = [
        (math.inf if row['abandoned'] else float(row['time_seconds']), row['completed'])
        for row in rows
        if row['abandoned'] or row['time_seconds'] is not None
    ]
    median_time, reached = _censored_median(time_observations)
    # Help and unfinished use the same cohort: players who started at least
    # UNFINISHED_WINDOW ago. Players still early in a game have not reached
    # for hints yet, so counting them would make a fresh edition look easy.
    settled = [row for row in rows if row['unfinished'] is not None]
    return {
        'n': n,
        'median_time': median_time,
        'median_time_censored': bool(time_observations) and not reached,
        'median_attempts': (
            _median([row['attempts'] for row in rows if row['attempts'] is not None])
            if game_id == 'censorly' else None
        ),
        'median_errors': (
            _median([row['errors'] for row in rows if row['errors'] is not None])
            if error_available else None
        ),
        'help_rate': (sum(bool(row['used_help']) for row in settled) / len(settled)) if settled else None,
        'unfinished_rate': (sum(bool(row['unfinished']) for row in settled) / len(settled)) if settled else None,
        'completed_n': sum(bool(row['completed']) for row in rows),
        'abandoned_n': sum(bool(row['abandoned']) for row in rows),
        'unfinished_denominator': len(settled),
        'error_available': error_available,
    }


def calculate_observed_metrics(placement, *, now=None):
    """Aggregate one row per actor for a single daily-game edition."""
    return aggregate_actor_results(
        placement.game_id,
        observe_actor_results(placement, now=now),
    )


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
    rows = DailyGameDifficulty.objects.filter(
        placement__game_id=game_id,
        placement__task_group__random_alphabetty_game__isnull=True,
        placement__task_group__random_censorly_game__isnull=True,
    ).only('n', 'payload')
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
    row.typical_attempts = typical['median_attempts']
    row.typical_errors = typical['median_errors']
    row.typical_help_rate = typical['help_rate']
    row.typical_unfinished_rate = typical['unfinished_rate']
    row.calculated_at = now
    row.payload = payload
    row.version = int(row.version or 0) + 1
    row.save(update_fields=[
        'typical_time', 'typical_attempts', 'typical_errors', 'typical_help_rate',
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
        'median_attempts': row.typical_attempts if row else None,
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


def classic_difficulty_ratios(metrics, norm):
    """Edition aggregate against the game type's historical norm."""
    time_ratio = _ratio(metrics.get('median_time'), norm.get('median_time'))
    if metrics.get('median_time_censored'):
        time_ratio = _censored_ratio(time_ratio)
    return {
        'time': time_ratio,
        'attempts': _ratio(metrics.get('median_attempts'), norm.get('median_attempts')),
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


def _rate_ratio(observed, expected):
    if not observed:
        return None
    observed_rate = sum(observed) / len(observed)
    expected_rate = sum(expected) / len(expected)
    return (observed_rate + RATE_EPSILON) / (expected_rate + RATE_EPSILON)


def _actor_histories(placement, actor_keys, *, published_at):
    """Up to ``PAIRED_HISTORY_WINDOW`` earlier outcomes per player, newest first."""
    histories = defaultdict(list)
    if published_at is None or not actor_keys:
        return histories
    keys = sorted(actor_keys)
    for start in range(0, len(keys), PAIRED_ACTOR_CHUNK):
        rows = (
            DailyDifficultyActorResult.objects.filter(
                game_id=placement.game_id,
                actor__in=keys[start:start + PAIRED_ACTOR_CHUNK],
                published_at__lt=published_at,
            )
            .exclude(placement_id=placement.pk)
            .order_by('actor', '-published_at')
            .values(
                'actor', 'completed', 'abandoned', 'time_seconds',
                'attempts', 'errors', 'used_help', 'unfinished',
            )
        )
        for row in rows.iterator():
            history = histories[row['actor']]
            if len(history) < PAIRED_HISTORY_WINDOW:
                history.append(row)
    return histories


def paired_difficulty_ratios(actor_rows, histories):
    """Each returning player against their own earlier editions of the game.

    A ratio of 1 means "as usual for the players who came", which keeps the
    scale of the classic ratios. Time is again a censored median, of per-player
    log ratios to the player's own median solve time.
    """
    time_observations = []
    effort = {'attempts': [], 'errors': []}
    help_observed, help_expected = [], []
    unfinished_observed, unfinished_expected = [], []
    for row in actor_rows or ():
        past = histories.get(row['actor']) or []
        if len(past) < PAIRED_MIN_HISTORY:
            continue
        solved_times = [item['time_seconds'] for item in past if item['completed'] and item['time_seconds']]
        if len(solved_times) >= PAIRED_MIN_HISTORY:
            baseline = math.log(statistics.median(solved_times))
            if row['abandoned']:
                time_observations.append((math.inf, False))
            elif row['time_seconds']:
                time_observations.append((math.log(row['time_seconds']) - baseline, row['completed']))
        for component in effort:
            past_values = [item[component] for item in past if item[component] is not None]
            if row[component] is not None and len(past_values) >= PAIRED_MIN_HISTORY:
                effort[component].append((row[component] + 1.0) / (statistics.median(past_values) + 1.0))
        settled_past = [item for item in past if item['unfinished'] is not None]
        if row['unfinished'] is None or len(settled_past) < PAIRED_MIN_HISTORY:
            continue
        help_observed.append(bool(row['used_help']))
        help_expected.append(sum(bool(item['used_help']) for item in settled_past) / len(settled_past))
        unfinished_observed.append(bool(row['unfinished']))
        unfinished_expected.append(sum(bool(item['unfinished']) for item in settled_past) / len(settled_past))

    log_median, reached = _censored_median(time_observations)
    time_ratio = None if log_median is None else math.exp(log_median)
    if time_observations and not reached:
        time_ratio = _censored_ratio(time_ratio)
    return {
        'ratios': {
            'time': time_ratio,
            'attempts': _median(effort['attempts']),
            'errors': _median(effort['errors']),
            'help': _rate_ratio(help_observed, help_expected),
            'unfinished': _rate_ratio(unfinished_observed, unfinished_expected),
        },
        'n': {
            'time': len(time_observations),
            'attempts': len(effort['attempts']),
            'errors': len(effort['errors']),
            'help': len(help_observed),
            'unfinished': len(unfinished_observed),
        },
    }


def blend_difficulty_ratios(classic, paired):
    """Geometric blend, weighted by how many returning players back each ratio."""
    ratios = {}
    weights = {}
    for component in PAIRED_COMPONENTS:
        classic_ratio = classic.get(component)
        paired_ratio = paired['ratios'].get(component)
        paired_n = paired['n'].get(component) or 0
        if paired_ratio is None or paired_ratio <= 0 or not paired_n:
            ratios[component], weights[component] = classic_ratio, 0.0
        elif classic_ratio is None or classic_ratio <= 0:
            ratios[component], weights[component] = paired_ratio, 1.0
        else:
            weight = paired_n / (paired_n + PAIRED_PRIOR_N)
            ratios[component] = math.exp(
                weight * math.log(paired_ratio) + (1.0 - weight) * math.log(classic_ratio)
            )
            weights[component] = weight
    return ratios, weights


def rate_difficulty_metrics(game_id, metrics, norm, *, ratios=None):
    if ratios is None:
        ratios = classic_difficulty_ratios(metrics, norm)
    scores = {
        'time': ratio_score(ratios['time'], TIME_RATIO_BOUNDS),
        'attempts': ratio_score(ratios['attempts'], EFFORT_RATIO_BOUNDS),
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
    # Help alone is not an outcome: right after release nobody has finished or
    # given up yet, and a rating built on hints only used to read as "easy".
    has_outcome = scores['time'] is not None or scores['unfinished'] is not None
    is_visible = n >= MIN_DISPLAY_N and has_outcome
    return {
        'ratios': ratios,
        'scores': scores,
        'weights': weights,
        'raw_rating': raw_rating,
        'confidence': confidence,
        'adjusted_rating': adjusted,
        'stars': stars,
        'is_visible': is_visible,
        'is_preliminary': is_visible and n < PRELIMINARY_UNTIL_N,
    }


def calculate_game_difficulty(placement, *, now=None, metrics=None, actor_rows=None, save=False):
    """Calculate a structured rating for one ``GameTaskGroup`` placement.

    ``save=True`` is for admin/manual rebuilds. Request handlers must not call
    this; they read ``DailyGameDifficulty`` via ``get_game_difficulty``.

    The result carries ``actor_rows`` for ``persist_difficulty_snapshot``,
    which stores them and removes the key before writing the payload.
    """
    if placement.game_id not in SUPPORTED_GAME_IDS or _is_random_daily_placement(placement):
        return None
    now = now or timezone.now()
    if metrics is None:
        actor_rows = observe_actor_results(placement, now=now)
        metrics = aggregate_actor_results(placement.game_id, actor_rows)
    norm = historical_norm(placement, metrics, now=now)
    classic = classic_difficulty_ratios(metrics, norm)
    histories = _actor_histories(
        placement,
        {row['actor'] for row in actor_rows or ()},
        published_at=published_at_for_placement(placement),
    )
    paired = paired_difficulty_ratios(actor_rows, histories)
    ratios, blend_weights = blend_difficulty_ratios(classic, paired)
    rating = rate_difficulty_metrics(placement.game_id, metrics, norm, ratios=ratios)
    result = {
        'game_id': placement.game_id,
        'placement_id': placement.pk,
        'number': placement.number,
        'n': metrics['n'],
        'metrics': metrics,
        'typical': norm,
        'norm_version': int(norm.get('version') or 0),
        'ratios_classic': classic,
        'ratios_paired': paired['ratios'],
        'paired_n': paired['n'],
        'blend_weights': blend_weights,
        **rating,
        'actor_rows': actor_rows,
    }
    if save:
        persist_difficulty_snapshot(placement, result, now=now, force=True)
    return result


def _snapshot_write_defaults(placement, result, *, now, published_at=None, changed_at=None, interval=None):
    published_at = published_at if published_at is not None else published_at_for_placement(placement)
    interval = interval or difficulty_refresh_interval(published_at, now, changed_at=changed_at)
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


def _replace_actor_results(placement, actor_rows, *, published_at):
    DailyDifficultyActorResult.objects.filter(placement=placement).delete()
    DailyDifficultyActorResult.objects.bulk_create(
        [
            DailyDifficultyActorResult(
                placement=placement,
                game_id=placement.game_id,
                published_at=published_at,
                **row,
            )
            for row in actor_rows
        ],
        batch_size=500,
    )


def persist_difficulty_snapshot(
    placement,
    result,
    *,
    now=None,
    claimed_revision=None,
    claim_token=None,
    force=False,
):
    """Write a calculated snapshot and, when present, its per-player rows.

    Cron workers pass ``claim_token`` so a stale worker cannot overwrite a
    newer claim. ``dirty`` is cleared only when ``data_revision`` still equals
    the revision this worker calculated; a newer attempt during calculation
    keeps the row eligible.
    """
    now = now or timezone.now()
    actor_rows = result.pop('actor_rows', None)
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
        changed_at=snapshot.content_changed_at,
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
    with transaction.atomic():
        updated = DailyGameDifficulty.objects.filter(**filters).update(**defaults)
        if updated and actor_rows is not None:
            _replace_actor_results(placement, actor_rows, published_at=snapshot.published_at)
    if updated:
        result['snapshot_id'] = snapshot.pk
    return updated


def save_observed_metrics(placement, metrics, *, now=None, actor_rows=None):
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
    with transaction.atomic():
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
        if actor_rows is not None:
            _replace_actor_results(placement, actor_rows, published_at=snapshot.published_at)
    snapshot.n = metrics['n']
    snapshot.payload = payload
    return snapshot


def _supported_placements(*, game_ids=None):
    game_ids = tuple(game_ids or SUPPORTED_GAME_IDS)
    placements = list(
        GameTaskGroup.objects.filter(
            game_id__in=game_ids,
            task_group__tasks__task_type__in=[TASK_TYPE_BY_GAME_ID[g] for g in game_ids],
            task_group__random_alphabetty_game__isnull=True,
            task_group__random_censorly_game__isnull=True,
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


def difficulty_refresh_interval(published_at, now, *, changed_at=None):
    """Maximum refresh frequency for a dirty game of this age.

    Age counts from publication or from the last task edit, whichever is
    newer, so an edited edition is followed as closely as a fresh one.
    Clean games are not recalculated merely because this interval elapsed.
    """
    starts = [moment for moment in (published_at, changed_at) if moment is not None]
    if published_at is None or not starts:
        return REFRESH_INTERVAL_OLD
    age = now - max(starts)
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
    if (
        placement is None
        or placement.game_id not in SUPPORTED_GAME_IDS
        or _is_random_daily_placement(placement)
    ):
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
        game.task_group_links.select_related('game', 'task_group').filter(
            task_group__random_alphabetty_game__isnull=True,
            task_group__random_censorly_game__isnull=True,
        )
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
        actor_rows = observe_actor_results(row, now=now)
        metrics = aggregate_actor_results(row.game_id, actor_rows)
        observed[row.pk] = (metrics, actor_rows)
        # Per-player rows of every edition must exist before pass two, which
        # compares each player against their other editions.
        save_observed_metrics(row, metrics, now=now, actor_rows=actor_rows)
    for game_id in tuple(game_ids or SUPPORTED_GAME_IDS):
        refresh_historical_norm(game_id, now=now)
    results = []
    for row in placements:
        metrics, actor_rows = observed[row.pk]
        result = calculate_game_difficulty(
            row,
            now=now,
            metrics=metrics,
            actor_rows=actor_rows,
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
    """Mark difficulty dirty, bumping the revision only when it is needed.

    Repeated attempts can arrive while a row is already waiting for the next
    refresh.  They do not need a new revision unless a refresh claim is active:
    in that case the revision must move so the in-flight calculation cannot
    clear the dirty flag for newer source data.
    """
    queryset = _difficulty_queryset(
        task_id=task_id,
        game_id=game_id,
        task_group_id=task_group_id,
        placement_id=placement_id,
    )
    return queryset.filter(
        Q(dirty=False) | Q(refresh_claim_token__isnull=False),
    ).update(
        data_revision=F('data_revision') + 1,
        dirty=True,
    )


def mark_game_difficulty_content_changed(*, task_group_id, now=None):
    """A task of the edition was edited: follow it as closely as a fresh release.

    The edit restarts the fast refresh cadence. A published edition also skips
    the throttle it was waiting on; an unpublished one keeps its first-run
    deadline at publication.
    """
    now = now or timezone.now()
    queryset = _difficulty_queryset(task_group_id=task_group_id)
    changed = mark_game_difficulty_changed(task_group_id=task_group_id)
    queryset.update(content_changed_at=now)
    queryset.filter(published_at__lte=now, refresh_not_before__gt=now).update(
        refresh_not_before=now,
    )
    return changed


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
        tooltip = 'Сложность: {}'.format(label)
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
        and not _is_random_daily_placement(placement)
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
    if (
        placement is None
        or placement.game_id not in SUPPORTED_GAME_IDS
        or _is_random_daily_placement(placement)
    ):
        return None
    if force:
        result = calculate_game_difficulty(placement, now=now, save=True)
        return _public_context(result)
    snapshot = DailyGameDifficulty.objects.filter(placement=placement).first()
    return _snapshot_public_context(snapshot)


def refresh_due_daily_difficulties(*, now=None, limit=DUE_REFRESH_LIMIT, game_ids=None, dry_run=False):
    from games.difficulty_domain.refresh import refresh_due_daily_difficulties as _refresh
    return _refresh(now=now, limit=limit, game_ids=game_ids, dry_run=dry_run)
