"""Aggregated post-completion statistics for the three daily games."""

import json
import logging
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Callable

from django.core.cache import caches
from django.db import connection, transaction
from django.db.models import Min, Q

from games.daily.page.section import ADVENT_SCHEDULE, CRYPTIC_SCHEDULE
from games.daily.registry import DAILY_GAME_REGISTRY
from games.models import Attempt, ChainTaskState, DailySolveTiming, PlayerCompletedGame, Task
from games.puzzles.alphabetty.core import normalize_word
from games.puzzles.alphabetty.play import (
    hint_count as alphabetty_hint_count,
    load_state as load_alphabetty_state,
    secret_from_task,
    split_ladder,
)
from games.puzzles.core.check_runtime import KEEP_GOING_STATE
from games.puzzles.raddle.engine import load_raddle_state, parse_raddle_data, resolve_assist_tiers
from games.puzzles.salad.rules import (
    load_state as load_salad_state,
    longest_dictionary_words,
    parse_task_puzzle,
)

CACHE_VERSION = 25
CRYPTIC_GAME_ID = CRYPTIC_SCHEDULE.game_id
# Simple daily games, one ordinary task a day: Криптики and the advent hunt
# share the cryptic statistics (attempts, hints, popular wrong answers).
SINGLE_ANSWER_GAME_IDS = frozenset({CRYPTIC_GAME_ID, ADVENT_SCHEDULE.game_id})
CACHE_TIMEOUT = 10 * 60
REVISION_TIMEOUT = CACHE_TIMEOUT * 2
CACHE_BUILD_LOCK_TIMEOUT = 30
CACHE_BUILD_WAIT_SECONDS = 8
CACHE_BUILD_POLL_INTERVAL = 0.1
_RELEASE_CACHE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
POPULAR_LIMIT = 20
POPULAR_MIN_ENTRIES = 5
CENSORLY_POPULAR_MIN_ENTRIES = 1
BOUND_WORDS_LIMIT = 10
# Cached per-actor results for personal comparisons; never sent to clients.
RANKING_KEY = '_ranking'
# Fewer solvers make a percentile noise, and a low one only discourages.
RANKING_MIN_SOLVED = 10
RANKING_MIN_PERCENT = 25
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyStatisticsAdapter:
    """Dispatch entry for one daily game's aggregate statistics."""

    key: str
    build: Callable


DAILY_STATISTICS_ADAPTERS = {}


def get_daily_statistics_adapter(task_type):
    """Return the registered statistics adapter for a task type."""
    for definition in DAILY_GAME_REGISTRY.all():
        if definition.task_type == task_type:
            adapter = DAILY_STATISTICS_ADAPTERS.get(definition.statistics_adapter_key)
            if adapter is None:
                return None
            return adapter
    return None


def cache_key(game_id, task_group_id):
    return 'daily-statistics:v{}:{}:{}'.format(CACHE_VERSION, game_id, task_group_id)


def _cache_revision_key(game_id, task_group_id):
    return '{}:revision'.format(cache_key(game_id, task_group_id))


def _versioned_cache_key(game_id, task_group_id, revision, published_at=None):
    publication = published_at.isoformat() if published_at is not None else 'none'
    return '{}:{}:{}'.format(
        cache_key(game_id, task_group_id), int(revision), publication,
    )


def _release_build_lock(cache, lock_key, lock_token):
    """Release our lock atomically on Redis; preserve a generic-cache fallback."""
    backend = getattr(cache, '_cache', None)
    get_client = getattr(backend, 'get_client', None)
    serializer = getattr(backend, '_serializer', None)
    if callable(get_client) and serializer is not None:
        client = get_client(cache.make_key(lock_key), write=True)
        client.eval(
            _RELEASE_CACHE_LOCK_SCRIPT,
            1,
            cache.make_key(lock_key),
            serializer.dumps(lock_token),
        )
        return

    # LocMem and other cache backends do not expose compare-and-delete. The
    # fallback is process-local and the lock is only a duplicate-work guard.
    if cache.get(lock_key) == lock_token:
        cache.delete(lock_key)


def _build_daily_statistics_observed(game, task_group, *, published_at=None):
    """Run one canonical build and record its database cost without SQL text."""
    query_count = 0
    query_seconds = 0.0
    started = time.perf_counter()

    def observe(execute, sql, params, many, context):
        nonlocal query_count, query_seconds
        query_count += 1
        query_started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            query_seconds += time.perf_counter() - query_started

    try:
        with connection.execute_wrapper(observe):
            result = _build_daily_statistics_payload(
                game, task_group, published_at=published_at,
            )
    except Exception:
        logger.exception(
            'daily_statistics_build_failed game=%s task_group=%s elapsed_ms=%.1f '
            'sql_count=%s sql_ms=%.1f',
            game.pk, task_group.pk, (time.perf_counter() - started) * 1000,
            query_count, query_seconds * 1000,
        )
        raise
    logger.info(
        'daily_statistics_build game=%s task_group=%s elapsed_ms=%.1f '
        'sql_count=%s sql_ms=%.1f',
        game.pk, task_group.pk, (time.perf_counter() - started) * 1000,
        query_count, query_seconds * 1000,
    )
    return result


def _latest_key(game_id, task_group_id, published_at=None):
    publication = published_at.isoformat() if published_at is not None else 'none'
    return '{}:latest:{}'.format(cache_key(game_id, task_group_id), publication)


def _store_generation(cache, game_id, task_group_id, revision, published_at, result):
    """Cache one generation and remember it as the newest one built."""
    cache.set(
        _versioned_cache_key(game_id, task_group_id, revision, published_at),
        result, CACHE_TIMEOUT,
    )
    latest_key = _latest_key(game_id, task_group_id, published_at)
    current = cache.get(latest_key)
    if not isinstance(current, dict) or int(current.get('revision', -1)) <= int(revision):
        cache.set(latest_key, {'revision': int(revision), 'payload': result}, CACHE_TIMEOUT)


def _latest_stale_payload(cache, game_id, task_group_id, revision, published_at):
    """The newest older generation, marked stale, for a page request.

    Every completion advances the revision and queues a background refresh,
    so during active play the exact previous revision is often not the one
    that was built. Page requests used to build synchronously then (and to
    rebuild again when another completion landed mid-build): bursts of
    5-12 s requests. They now show the newest built generation instead.
    """
    entry = cache.get(_latest_key(game_id, task_group_id, published_at))
    if not isinstance(entry, dict) or int(entry.get('revision', -1)) >= int(revision):
        return None
    floor = cache.get(_stale_floor_key(game_id, task_group_id))
    if floor is not None and int(entry['revision']) < int(floor):
        return None
    payload = entry.get('payload')
    if isinstance(payload, dict):
        payload = dict(payload)
        payload['_stale'] = True
    return payload


def invalidate_daily_statistics(game_id, task_group_id, *, allow_stale=False):
    """Start a new statistics generation.

    ``allow_stale`` is for new or rechecked completions: pages may keep
    showing the newest built generation until the background refresh lands.
    Every other change (hidden profiles, authorship, resets) can remove a
    person from the statistics, so older generations stop being shown.
    """
    if not game_id or not task_group_id:
        return
    transaction.on_commit(
        lambda: _advance_daily_statistics_revision(
            game_id, task_group_id, allow_stale=allow_stale,
        ),
    )


def _stale_floor_key(game_id, task_group_id):
    return '{}:stale-floor'.format(cache_key(game_id, task_group_id))


def _advance_daily_statistics_revision(game_id, task_group_id, *, allow_stale=False):
    cache = caches['daily_statistics']
    revision_key = _cache_revision_key(game_id, task_group_id)
    revision = None
    try:
        try:
            cache.incr(revision_key)
        except ValueError:
            # Initialize the counter, then retry if another invalidation won
            # the first-write race.
            if cache.add(revision_key, 1, timeout=REVISION_TIMEOUT):
                revision = 1
            else:
                cache.incr(revision_key)
        # Results expire sooner than revision counters. Refreshing the counter
        # TTL bounds Redis key growth without allowing a live result generation
        # to become current again after its counter expires.
        if revision is None:
            revision = int(cache.get(revision_key, 0) or 0)
        cache.touch(revision_key, REVISION_TIMEOUT)
        if not allow_stale:
            cache.set(_stale_floor_key(game_id, task_group_id), revision, REVISION_TIMEOUT)
    except Exception:
        # Cache is an optimization. A cache outage must not fail game completion.
        logger.warning(
            'daily_statistics_cache_invalidation_failed game=%s task_group=%s',
            game_id, task_group_id, exc_info=True,
        )
    try:
        from games.realtime.projection import publish_daily_statistics_refresh

        publish_daily_statistics_refresh(
            game_id, task_group_id, revision=revision,
        )
    except Exception:
        # Statistics are still correct on demand; queue publication is an
        # optimization and must never fail the canonical game completion.
        logger.warning(
            'daily_statistics_refresh_schedule_failed game=%s task_group=%s',
            game_id, task_group_id, exc_info=True,
        )


def _actor_key(row):
    if getattr(row, 'team_id', None):
        return ('team', row.team_id)
    if row.user_id:
        return ('user', row.user_id)
    return ('anon', str(row.anon_key))


def _median(values):
    return round(float(median(values)), 1) if values else None


def _mean(values):
    return round(float(mean(values)), 1) if values else None


def _pct(n, total):
    return round(100.0 * n / total, 1) if total else 0


def _personal_comparison(value, median_value, others):
    """Player's result against the median; lower is better."""
    if value is None or median_value is None:
        return None
    out = {'value': value, 'median': median_value, 'better_percent': None, 'best': False}
    # ``others`` is None when the player is outside the public cohort (hidden,
    # author, pre-publication): their result still compares with the median,
    # but is not ranked against the cohort.
    if others and len(others) + 1 >= RANKING_MIN_SOLVED:
        worse = sum(1 for other in others if other > value)
        if worse == len(others):
            out['best'] = True
        else:
            # Floored and over strictly worse results, so ties never inflate it.
            percent = worse * 100 // len(others)
            if percent >= RANKING_MIN_PERCENT:
                out['better_percent'] = percent
    return out


def personal_attempt_count(game, task_group, *, actor_key):
    """Count official guesses even for players excluded from public results."""
    if game.id in SINGLE_ANSWER_GAME_IDS:
        task = Task.objects.filter(task_group=task_group).order_by('id').first()
        if task is None:
            return None
        return single_answer_attempt_count(
            _attempt_rows(task, game, [actor_key], CRYPTIC_STATUSES).only('id', 'status', 'state'),
        )
    task = Task.objects.filter(
        task_group=task_group, task_type__in=('alphabetty', 'censorly'),
    ).order_by('id').first()
    if task is None:
        return None
    statuses = ('Ok', 'Partial', 'Wrong') if task.task_type == 'censorly' else ('Ok', 'Partial')
    rows = _attempt_rows(task, game, [actor_key], statuses)
    if not rows.filter(status='Ok').exists():
        return None
    if task.task_type == 'censorly':
        rows = rows.exclude(text__isnull=True).exclude(text='').exclude(text__startswith='#hint:')
    return rows.count()


def personal_in_cohort(game, task_group, *, actor_key, published_at=None):
    """Whether the player counts in the public statistics cohort right now."""
    return actor_key in _completed_actors(
        game, task_group, published_at=published_at, only_keys=[actor_key],
    )


def personal_censorly_perfect(game, task_group, *, actor_key, attempts):
    """Same «perfect» rule as ``_censorly`` for a cohort player missing from its ranking.

    A just-finished player is absent while the previous cached generation is
    still served, so their perfect solve is checked on its own.
    """
    from games.puzzles.censorly.play import hint_count, load_state, minimal_title_attempts, puzzle_from_task

    if not attempts:
        return False
    task = Task.objects.filter(
        task_group=task_group, task_type='censorly',
    ).order_by('id').first()
    if task is None:
        return False
    actors = [actor_key]
    state = _chain_states(task, game, actors).get(actor_key)
    if state is None:
        state = _latest_attempt_states(task, game, actors, ('Ok', 'Partial', 'Wrong')).get(actor_key)
    if hint_count(load_state(state)) != 0:
        return False
    try:
        minimum_attempts = minimal_title_attempts(puzzle_from_task(task) or {}, task=task)
    except Exception:
        logger.warning('censorly_minimal_attempts_failed task=%s', task.pk, exc_info=True)
        return False
    return bool(minimum_attempts) and attempts <= minimum_attempts


def personal_statistics(payload, *, actor_key, personal_time_seconds, personal_attempts=None,
                        personal_perfect=False, in_cohort=None):
    """Public statistics payload with the requesting player's comparisons.

    ``in_cohort`` tells a just-finished player, missing from a stale cached
    ranking but still ranked, from one outside the public cohort. When it is
    None, presence in the ranking decides.
    """
    ranking = payload.get(RANKING_KEY) or {}
    summary = payload.get('summary') or {}
    public = {key: value for key, value in payload.items() if key != RANKING_KEY}
    times = ranking.get('times') or {}

    def ranked_against(values):
        ranked = actor_key in values if in_cohort is None else in_cohort
        return [value for key, value in values.items() if key != actor_key] if ranked else None

    comparison = {'time': _personal_comparison(
        personal_time_seconds, summary.get('median_time_seconds'), ranked_against(times),
    )}
    if 'median_attempts' in summary:
        attempts = ranking.get('attempts') or {}
        attempt_comparison = _personal_comparison(
            attempts.get(actor_key, personal_attempts), summary.get('median_attempts'),
            ranked_against(attempts),
        )
        if attempt_comparison is not None:
            if actor_key in attempts:
                attempt_comparison['perfect'] = actor_key in (ranking.get('perfect') or ())
            else:
                attempt_comparison['perfect'] = bool(in_cohort and personal_perfect)
            if payload.get('kind') == 'alphabet' and attempt_comparison['value'] == 1:
                # The first guess carries no information, so a one-guess
                # Алфавитка is luck or a peek: compare it, do not praise it.
                attempt_comparison.update(best=False, better_percent=None)
        comparison['attempts'] = attempt_comparison
    public['comparison'] = comparison
    public['achievements'] = _achievements(comparison)
    return public


# Shown with an icon in the statistics and as an emoji line in «Ваш результат»,
# its copied text and the story card.
ACHIEVEMENTS = {
    'fastest': {'emoji': '⚡️', 'icon': 'lightning', 'text': 'Быстрее всех!'},
    'fewest_attempts': {'emoji': '🏆', 'icon': 'trophy', 'text': 'Меньше всех попыток!'},
    'perfect': {'emoji': '🎯', 'icon': 'target', 'text': 'Без лишних попыток!'},
}


def _achievements(comparison):
    codes = []
    if (comparison.get('time') or {}).get('best'):
        codes.append('fastest')
    attempts = comparison.get('attempts') or {}
    if attempts.get('perfect'):
        codes.append('perfect')
    elif attempts.get('best'):
        codes.append('fewest_attempts')
    return [{'code': code, **ACHIEVEMENTS[code]} for code in codes]


def build_attempt_histogram(values, tail_from=30):
    """Build exact buckets below tail_from and one bucket for its upper tail."""
    if tail_from < 2:
        raise ValueError('tail_from must be at least 2')

    counts = Counter(int(value) for value in values if int(value) > 0)
    if not counts:
        return []

    ranges = [(value, value) for value in range(1, tail_from)]
    ranges.append((tail_from, None))

    total = sum(counts.values())
    bucket_counts = []
    for bucket_start, bucket_end in ranges:
        count = sum(
            count for value, count in counts.items()
            if value >= bucket_start and (bucket_end is None or value <= bucket_end)
        )
        bucket_counts.append(count)

    percentages = [_pct(count, total) for count in bucket_counts]
    rounding_delta = round(100 - sum(percentages), 1)
    if rounding_delta:
        percentages[max(range(len(bucket_counts)), key=lambda index: bucket_counts[index])] += rounding_delta
    largest_count = max(bucket_counts)

    histogram = []
    for (bucket_start, bucket_end), count, percent in zip(ranges, bucket_counts, percentages):
        histogram.append({
            'from': bucket_start,
            'to': bucket_end,
            'count': count,
            'label': '{}+'.format(bucket_start) if bucket_end is None else str(bucket_start),
            'percent': percent,
            'bar_percent': round(100.0 * count / largest_count, 1),
        })
    return histogram


def _build_censorly_attempt_histogram(values):
    """Group Censorly attempts into five-attempt buckets through 150."""
    counts = Counter(int(value) for value in values if int(value) > 0)
    if not counts:
        return []

    ranges = [(start, start + 4) for start in range(1, 151, 5)]
    ranges.append((151, None))
    total = sum(counts.values())
    bucket_counts = [
        sum(
            count for value, count in counts.items()
            if value >= start and (end is None or value <= end)
        )
        for start, end in ranges
    ]
    percentages = [_pct(count, total) for count in bucket_counts]
    rounding_delta = round(100 - sum(percentages), 1)
    if rounding_delta:
        percentages[max(range(len(bucket_counts)), key=lambda index: bucket_counts[index])] += rounding_delta
    largest_count = max(bucket_counts)
    return [
        {
            'from': start,
            'to': end,
            'count': count,
            'label': '{}+'.format(start) if end is None else '{}–{}'.format(start, end),
            'percent': percent,
            'bar_percent': round(100.0 * count / largest_count, 1),
        }
        for (start, end), count, percent in zip(ranges, bucket_counts, percentages)
    ]


def _results_actor(row):
    """Same participant object the results table uses for eligibility."""
    from games.models import PersonalResultsParticipant

    if row.team_id:
        return row.team
    if row.user_id:
        return PersonalResultsParticipant(user_id=row.user_id)
    if row.anon_key:
        return PersonalResultsParticipant(anon_key=row.anon_key)
    return None


def _completed_actors(game, task_group, published_at=None, only_keys=None):
    rows = PlayerCompletedGame.objects.filter(
        game=game, task_group=task_group,
        result=PlayerCompletedGame.RESULT_SOLVED,
    )
    if only_keys is not None:
        rows = rows.filter(_actor_q(only_keys))
    rows = list(rows.select_related('team').only('team_id', 'user_id', 'anon_key', 'team'))
    from games.results.standings.leaderboard import actor_key, eligible_public_actors

    actor_pairs = [
        (_actor_key(row), actor)
        for row in rows
        for actor in [_results_actor(row)]
        if actor is not None
    ]
    actors = [actor for _key, actor in actor_pairs]
    result_times = {}
    if published_at is not None and actors:
        actors_by_key = dict(actor_pairs)
        # Group every actor of the release and pick ours in Python. An OR of
        # one exact-actor clause per solver (hundreds) made MySQL test each
        # attempt row against all of them: ~0.9 s per cold statistics build,
        # the costliest query shape on the database.
        task_ids = list(
            Task.objects.filter(task_group=task_group).values_list('id', flat=True)
        )
        first_attempts = Attempt.manager.filter(
            game=game,
            task_id__in=task_ids,
            skip=False,
            status__in=('Ok', 'Partial', 'Wrong'),
            replay_slot__isnull=True,
        ).values(
            'team_id', 'user_id', 'anon_key',
        ).annotate(first_attempt=Min('time'))
        for row in first_attempts:
            team_id, user_id, anon_key = row['team_id'], row['user_id'], row['anon_key']
            # Same exact actor shapes as _actor_q.
            if team_id is not None and user_id is None and anon_key is None:
                key = ('team', team_id)
            elif user_id is not None and team_id is None and anon_key is None:
                key = ('user', user_id)
            elif anon_key is not None and team_id is None and user_id is None:
                key = ('anon', anon_key)
            else:
                continue
            actor = actors_by_key.get(key)
            if actor is not None and row['first_attempt'] is not None:
                result_times[actor] = row['first_attempt']
    eligible = {
        actor_key(actor)
        for actor in eligible_public_actors(
            actors,
            task_group=task_group,
            game=game if published_at is not None else None,
            published_at=published_at,
            result_times=result_times,
        )
    }
    return {
        _actor_key(row): row
        for row in rows
        if _actor_key(row) in eligible
    }


def _actor_q(keys):
    q = Q()
    for kind, value in keys:
        if kind == 'team':
            q |= Q(team_id=value, user__isnull=True, anon_key__isnull=True)
        elif kind == 'user':
            q |= Q(user_id=value, team__isnull=True, anon_key__isnull=True)
        else:
            q |= Q(anon_key=value, team__isnull=True, user__isnull=True)
    return q


def _timing_actor_q(keys):
    q = Q()
    for kind, value in keys:
        if kind == 'team':
            q |= Q(team_id=value, user__isnull=True, anon_key__isnull=True)
        elif kind == 'user':
            q |= Q(user_id=value, anon_key__isnull=True)
        elif kind == 'anon':
            q |= Q(anon_key=value, user__isnull=True)
    return q


# Above this many actors the per-actor OR clause costs more than reading the
# task's attempts by index and keeping the actors in Python: MySQL tested up
# to 27k rows against hundreds of clauses (1.7 s per query, 2026-11-19).
_ACTOR_OR_LIMIT = 20


def _attempt_rows(task, game, actors, statuses):
    """Attempts of ``actors``; large actor sets come back unfiltered.

    Callers group the rows through ``_grouped_by_actor(rows, actors)`` or
    ``_keeps_actor``, which apply the same exact actor shapes as ``_actor_q``.
    """
    rows = Attempt.manager.filter(
        task=task, game=game, skip=False, status__in=statuses,
        replay_slot__isnull=True,
    )
    if len(actors) <= _ACTOR_OR_LIMIT:
        rows = rows.filter(_actor_q(actors))
    return rows.order_by('time', 'id')


def _exact_actor_key(row):
    """``_actor_key`` for rows matching ``_actor_q``'s exact shapes, else None."""
    team_id, user_id, anon_key = row.team_id, row.user_id, row.anon_key
    if team_id is not None and user_id is None and anon_key is None:
        return ('team', team_id)
    if user_id is not None and team_id is None and anon_key is None:
        return ('user', user_id)
    if anon_key is not None and team_id is None and user_id is None:
        return ('anon', anon_key)
    return None


def _keeps_actor(row, actors):
    key = _exact_actor_key(row)
    return key is not None and key in actors


def _keeps_timing_actor(row, actors):
    """``_timing_actor_q``'s shapes: a user's timing row may carry a team."""
    team_id, user_id, anon_key = row.team_id, row.user_id, row.anon_key
    if team_id is not None and user_id is None and anon_key is None:
        key = ('team', team_id)
    elif user_id is not None and anon_key is None:
        key = ('user', user_id)
    elif anon_key is not None and user_id is None:
        key = ('anon', anon_key)
    else:
        return False
    return key in actors


def _grouped_by_actor(rows, actors=None):
    wanted = set(actors) if actors is not None else None
    grouped = defaultdict(list)
    for row in rows:
        if wanted is not None and not _keeps_actor(row, wanted):
            continue
        grouped[_actor_key(row)].append(row)
    return grouped


def _attempts_for(task, game, actors, statuses=('Ok', 'Partial')):
    if not actors:
        return {}
    return _grouped_by_actor(_attempt_rows(task, game, actors, statuses).only(
        'id', 'team_id', 'user_id', 'anon_key', 'text', 'state', 'status',
        'time', 'active_time_ms',
    ), actors)


def _completed_time_map(game, task_group, actors):
    """Actor key -> canonical solve seconds for completed players."""
    if not actors:
        return {}
    rows = DailySolveTiming.objects.filter(
        game=game, task_group=task_group, status=DailySolveTiming.STATUS_COMPLETED,
        replay_slot__isnull=True,
    )
    wanted_timing = None
    if len(actors) <= _ACTOR_OR_LIMIT:
        rows = rows.filter(_timing_actor_q(actors))
    else:
        wanted_timing = set(actors)
    rows = rows.only(
        'team_id', 'user_id', 'anon_key', 'status', 'frozen_ms', 'accumulated_ms', 'timing_version',
        'completed_at', 'updated_at', 'pk',
    )
    from games.daily.timing import canonical_elapsed_seconds, timing_row_preference

    values = {}
    missing_timing_keys = set()
    timing_preferences = {}
    for row in rows:
        if wanted_timing is not None and not _keeps_timing_actor(row, wanted_timing):
            continue
        if not row.timing_version:
            continue
        key = _actor_key(row)
        preference = timing_row_preference(row)
        if preference <= timing_preferences.get(key, ()):
            continue
        timing_preferences[key] = preference
        values.pop(key, None)
        missing_timing_keys.discard(key)
        seconds = canonical_elapsed_seconds(game=game, timing_row=row)
        if seconds is not None:
            values[key] = seconds
        else:
            missing_timing_keys.add(key)
    missing_timing_keys.difference_update(values)
    if missing_timing_keys:
        from games.results.share import elapsed_seconds_from_attempts

        attempts_by_actor = defaultdict(list)
        # The same attempts as the results table (leaderboard fallback).
        attempts = Attempt.manager.in_task_group(task_group).filter(
            game=game, skip=False, replay_slot__isnull=True,
        ).filter(_actor_q(missing_timing_keys)).only(
            'team_id', 'user_id', 'anon_key', 'time',
        ).order_by('time', 'id')
        for attempt in attempts:
            attempts_by_actor[_actor_key(attempt)].append(attempt)
        for key in missing_timing_keys:
            seconds = elapsed_seconds_from_attempts(attempts_by_actor.get(key, []))
            if seconds > 0:
                values[key] = seconds
    return values


def _chain_states(task, game, actors):
    rows = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general', replay_slot__isnull=True,
    )
    if len(actors) <= _ACTOR_OR_LIMIT:
        rows = rows.filter(_actor_q(actors))
        return {_actor_key(row): row.state for row in rows.only('team_id', 'user_id', 'anon_key', 'state')}
    wanted = set(actors)
    return {
        _actor_key(row): row.state
        for row in rows.only('team_id', 'user_id', 'anon_key', 'state')
        if _keeps_actor(row, wanted)
    }


def _latest_attempt_states(task, game, actors, statuses):
    """Latest non-empty attempt state per actor, without loading the others."""
    latest_ids = {}
    rows = _attempt_rows(task, game, actors, statuses).exclude(
        state__isnull=True,
    ).exclude(state='').only('id', 'team_id', 'user_id', 'anon_key')
    wanted = set(actors)
    for row in rows:
        if _keeps_actor(row, wanted):
            latest_ids[_actor_key(row)] = row.id
    rows = Attempt.manager.filter(id__in=latest_ids.values()).only(
        'id', 'team_id', 'user_id', 'anon_key', 'state',
    )
    return {_actor_key(row): row.state for row in rows}


def _latest_states(task, game, actors, attempts):
    states = _chain_states(task, game, actors)
    for key, values in attempts.items():
        if key not in states:
            for row in reversed(values):
                if row.state:
                    states[key] = row.state
                    break
    return states


def _salad(task, game, actors):
    grid, words, rare_words, near_words = parse_task_puzzle(task.checker_data, task.answer)
    near_set = {normalize_word(word) for word in near_words}
    attempts = _attempts_for(task, game, actors)
    states = _latest_states(task, game, actors, attempts)
    total = len(actors)
    no_hints = 0
    order = defaultdict(list)
    rare_counts = defaultdict(set)
    extra_counts = defaultdict(set)
    for actor in actors:
        state = load_salad_state(states.get(actor))
        hints = state.get('hint_counts') or {}
        if not hints:
            no_hints += 1
        solved_before = set()
        self_position = 0
        for row in attempts.get(actor, []):
            try:
                payload = json.loads(row.text or '{}')
                if (payload.get('action') or 'solve') != 'solve':
                    continue
                after = load_salad_state(row.state)
                added = set(after.get('solved_indices') or []) - solved_before
                for index in sorted(added):
                    # A hinted target is still counted in hint-rate, but is not
                    # a self-found order observation.
                    if int(hints.get(index, 0) or 0) <= 0:
                        self_position += 1
                        order[index].append(self_position)
                solved_before |= added
            except (TypeError, ValueError):
                continue
        for word in state.get('found_rare_words') or []:
            normalized = normalize_word(word)
            rare_counts[normalized].add(actor)
        for word in state.get('found_extra') or []:
            normalized = normalize_word(word)
            extra_counts[normalized].add(actor)
    findings = {}
    for word, players in rare_counts.items():
        if word:
            findings[word] = {'players': set(players), 'rare': True, 'near': word in near_set}
    for word, players in extra_counts.items():
        if word:
            finding = findings.setdefault(word, {'players': set(), 'rare': False, 'near': False})
            finding['players'].update(players)
    popular_findings = [
        {'word': word, 'players': len(item['players']), 'rare': item['rare'], 'near': item['near']}
        for word, item in sorted(findings.items(), key=lambda entry: (-len(entry[1]['players']), entry[0]))
        if len(item['players']) >= POPULAR_MIN_ENTRIES
    ][:POPULAR_LIMIT]
    rare_rows = [
        {'word': word, 'players': len(players), 'rare': True, 'near': word in near_set}
        for word, players in sorted(rare_counts.items(), key=lambda item: (-len(item[1]), item[0]))[:10]
        if word
    ]
    extra_rows = [
        {'word': word, 'players': len(players), 'rare': False}
        for word, players in sorted(extra_counts.items(), key=lambda item: (-len(item[1]), item[0]))[:10]
        if word
    ]
    found_words = {}
    for word in words:
        normalized = normalize_word(word)
        if normalized:
            found_words[normalized] = {'word': word, 'kind': 'answer'}
    for word in rare_counts:
        if word and word not in found_words:
            found_words[word] = {'word': word, 'kind': 'near' if word in near_set else 'rare'}
    for word in extra_counts:
        if word and word not in found_words:
            found_words[word] = {'word': word, 'kind': 'extra'}
    long_found = sorted(
        found_words.values(), key=lambda item: (-len(normalize_word(item['word'])), normalize_word(item['word']))
    )[:5]
    try:
        payload = json.loads(task.checker_data or '{}')
    except (TypeError, ValueError):
        payload = {}
    saved_missing = payload.get('longest_missing_words') or longest_dictionary_words(
        tuple(grid), tuple(normalize_word(word) for word in words + rare_words + near_words),
    )
    missing_found = set(found_words)
    long_missing = [
        {'word': word, 'kind': 'missing'}
        for word in saved_missing
        if normalize_word(word) not in missing_found
    ][:5]
    long_words = list(found_words.values())
    long_word_keys = set(found_words)
    for word in saved_missing:
        normalized = normalize_word(word)
        if normalized and normalized not in long_word_keys:
            long_words.append({'word': word, 'kind': 'missing'})
            long_word_keys.add(normalized)
    long_words.sort(
        key=lambda item: (-len(normalize_word(item['word'])), normalize_word(item['word']))
    )
    word_rows = [
        {'word': words[index], 'average_order': _mean(order[index]), 'hint_percent': _pct(sum(1 for actor in actors if int((load_salad_state(states.get(actor)).get('hint_counts') or {}).get(index, 0) or 0) > 0), total)}
        for index in range(len(words))
    ]
    word_rows.sort(key=lambda item: (item['average_order'] is None, item['average_order'] if item['average_order'] is not None else 0))
    solve_times = _completed_time_map(game, task.task_group, actors)
    return {
        'kind': 'salad', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(list(solve_times.values())), 'without_hints_percent': _pct(no_hints, total)},
        RANKING_KEY: {'times': solve_times},
        'words': word_rows,
        'popular_findings': popular_findings, 'rare': rare_rows, 'off_topic': extra_rows,
        'long_found': long_found, 'long_missing': long_missing, 'long_words': long_words[:20],
    }


def _ladder(task, game, actors):
    parsed = parse_raddle_data(task)
    attempts = _attempts_for(task, game, actors)
    total = len(actors)
    no_hints = 0
    times = defaultdict(list)
    hint_counts = defaultdict(int)
    last_word_counts = defaultdict(int)
    for actor in actors:
        assist = {}
        previous = set()
        previous_active = 0
        solved_order = []
        for row in attempts.get(actor, []):
            try:
                state = load_raddle_state(row.state, parsed['n_words'])
                current = set(state.get('solved_indices') or [])
                payload = json.loads(row.text or '{}')
                index = int(payload.get('word_index', -1))
            except (TypeError, ValueError):
                continue
            if index in current - previous:
                # States written by older clients may contain either string or
                # integer keys.  Normalize through the canonical helper.
                tier = resolve_assist_tiers(state).get(index, 0)
                assist[index] = max(assist.get(index, 0), tier)
                if tier == 0 and row.active_time_ms is not None and index not in (0, parsed['n_words'] - 1):
                    current_active = max(previous_active, int(row.active_time_ms))
                    times[index].append((current_active - previous_active) / 1000)
                if row.active_time_ms is not None:
                    previous_active = max(previous_active, int(row.active_time_ms))
                solved_order.extend(
                    solved_index
                    for solved_index in sorted(current - previous)
                    if solved_index not in (0, parsed['n_words'] - 1)
                )
            previous = current
        if not assist:
            no_hints += 1
        elif not any(assist.values()):
            no_hints += 1
        for index, tier in assist.items():
            if tier > 0:
                hint_counts[index] += 1
        if solved_order:
            last_word_counts[solved_order[-1]] += 1
    solve_times = _completed_time_map(game, task.task_group, actors)
    return {
        'kind': 'ladder', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(list(solve_times.values())), 'without_hints_percent': _pct(no_hints, total)},
        RANKING_KEY: {'times': solve_times},
        'word_stats_available': all(times[index] for index in range(1, parsed['n_words'] - 1)),
        'words': [
            {
                'word': parsed['words'][i],
                'given': i in (0, parsed['n_words'] - 1),
                'median_time_seconds': _median(times[i]),
                'hint_percent': _pct(hint_counts[i], total),
                'last_percent': _pct(last_word_counts[i], total),
            }
            for i in range(parsed['n_words'])
        ],
    }


def _popular_word_rows(counts, *, limit):
    return [
        {'word': word, 'players': len(players)}
        for word, players in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))
        if len(players) >= POPULAR_MIN_ENTRIES
    ][:limit]


def _pre_win_guesses(rows, answer):
    guesses = []
    for row in rows:
        if row.status == 'Ok' and normalize_word(row.text) == answer:
            break
        guess = normalize_word(row.text)
        if guess and guess != answer:
            guesses.append(guess)
    return guesses


def _alphabet(task, game, actors):
    attempts = _attempts_for(task, game, actors)
    states = _latest_states(task, game, actors, attempts)
    answer = secret_from_task(task)
    attempt_counts = []
    actor_attempts = {}
    guesses = defaultdict(set)
    above_bounds = defaultdict(set)
    below_bounds = defaultdict(set)
    for actor in actors:
        rows = attempts.get(actor, [])
        won = [row for row in rows if row.status == 'Ok']
        if not won:
            continue
        n = len(rows)
        attempt_counts.append(n)
        actor_attempts[actor] = n
        for row in rows:
            guess = normalize_word(row.text)
            if guess and guess != answer and row.status != 'Ok':
                guesses[guess].add(actor)
        pre_win = _pre_win_guesses(rows, answer)
        if pre_win and answer:
            earlier, later = split_ladder(pre_win, answer)
            if earlier:
                above_bounds[earlier[-1]].add(actor)
            if later:
                below_bounds[later[0]].add(actor)
    histogram = build_attempt_histogram(attempt_counts)
    no_hints = sum(
        1 for actor in actors
        if alphabetty_hint_count(load_alphabetty_state(states.get(actor))) == 0
    )
    popular_guesses = _popular_word_rows(guesses, limit=POPULAR_LIMIT)
    above_words = _popular_word_rows(above_bounds, limit=BOUND_WORDS_LIMIT)
    below_words = _popular_word_rows(below_bounds, limit=BOUND_WORDS_LIMIT)
    solve_times = _completed_time_map(game, task.task_group, actors)
    return {
        'kind': 'alphabet',
        'solved': len(actors),
        'summary': {
            'solved': len(actors),
            'median_attempts': _median(attempt_counts),
            'median_time_seconds': _median(list(solve_times.values())),
            'without_hints_percent': _pct(no_hints, len(actors)),
        },
        'distribution': histogram,
        RANKING_KEY: {'times': solve_times, 'attempts': actor_attempts},
        'guesses': popular_guesses,
        'above_words': above_words,
        'below_words': below_words,
    }


def _censorly(task, game, actors):
    """Group by lemma, displaying only submitted forms (excluding title answers)."""
    from games.puzzles.censorly.article.redact import title_opening_lemmas
    from games.puzzles.censorly.play import (
        hint_count as censorly_hint_count,
        load_state as load_censorly_state,
        minimal_title_attempts,
        puzzle_from_task,
    )
    from games.puzzles.censorly.text.normalize import lemma_of, normalize_surface
    from games.puzzles.censorly.text.tokenize import title_content_lemmas

    # Wrong guesses count toward a Цензурка solve too; hints are filtered
    # from attempt_counts below using their #hint: marker.
    statuses = ('Ok', 'Partial', 'Wrong')
    attempts = _grouped_by_actor(
        _attempt_rows(task, game, actors, statuses)
        .only('id', 'team_id', 'user_id', 'anon_key', 'text', 'status', 'time'),
        actors,
    ) if actors else {}
    states = _chain_states(task, game, actors) if actors else {}
    missing = [actor for actor in actors if actor not in states]
    if missing:
        # Actors without a chain state fall back to their latest attempt
        # state, as for the other games, loading one state per actor.
        states.update(_latest_attempt_states(task, game, missing, statuses))
    payload = puzzle_from_task(task) or {}
    title_lemmas = title_content_lemmas(payload)
    title_opening = title_opening_lemmas(payload)
    try:
        minimum_attempts = minimal_title_attempts(payload, task=task)
    except Exception:
        # A matcher failure must not take the whole statistics block down.
        logger.warning('censorly_minimal_attempts_failed task=%s', task.pk, exc_info=True)
        minimum_attempts = None
    attempt_counts = []
    actor_attempts = {}
    perfect = set()
    lemma_players = defaultdict(set)
    last_before_win = defaultdict(set)
    lemma_forms = defaultdict(Counter)
    submitted_forms = set()
    no_hints = 0

    for actor in actors:
        rows = attempts.get(actor, [])
        if not any(row.status == 'Ok' for row in rows):
            continue
        real_guesses = [
            row for row in rows
            if row.text and not str(row.text).startswith('#hint:')
        ]
        attempt_counts.append(len(real_guesses))
        actor_attempts[actor] = len(real_guesses)
        state = load_censorly_state(states.get(actor))
        # Each guess attempt stores its normalized word as ``text`` and the
        # history refuses repeats, so the player's one current state maps a
        # word to the article lemma it opened. Reading every attempt's own
        # state instead pulled ~120 MB of JSON per popular edition through
        # the 512 MB buffer pool on each rebuild (2026-11-28).
        guess_lemmas = {
            guess['word']: guess['lemma'] for guess in state.get('guesses') or []
        }

        def lemma_from_row(row, guess_lemmas=guess_lemmas):
            word = normalize_surface(row.text or '')
            return guess_lemmas.get(word) or lemma_of(word)

        if censorly_hint_count(state) == 0:
            no_hints += 1
            # A guess from outside the article may beat the computed minimum.
            if minimum_attempts and len(real_guesses) <= minimum_attempts:
                perfect.add(actor)
        for row in real_guesses:
            lemma = lemma_from_row(row)
            form = str(row.text).lower()
            submitted_forms.add(form)
            if lemma:
                lemma_forms[lemma][form] += 1
            if lemma and lemma not in title_lemmas:
                lemma_players[lemma].add(actor)
        last_eligible_lemma = ''
        for row in rows:
            if row.status == 'Ok':
                break
            if row.text and not str(row.text).startswith('#hint:'):
                lemma = lemma_from_row(row)
                if lemma and lemma not in title_opening:
                    last_eligible_lemma = lemma
        if last_eligible_lemma:
            last_before_win[last_eligible_lemma].add(actor)

    def display_words(players_by_lemma, minimum_players):
        players_by_word = defaultdict(set)
        for lemma, players in players_by_lemma.items():
            # Both lists use the same submitted spellings and frequencies.
            # Never expose internal matcher identities or unsubmitted lemmas.
            word = lemma.lower()
            if word not in submitted_forms:
                forms = lemma_forms[lemma]
                word = min(forms, key=lambda form: (-forms[form], form))
            players_by_word[word].update(players)
        return [
            {'word': word, 'players': len(players)}
            for word, players in sorted(
                players_by_word.items(), key=lambda item: (-len(item[1]), item[0]),
            )
            if len(players) >= minimum_players
        ][:POPULAR_LIMIT]

    popular = display_words(lemma_players, CENSORLY_POPULAR_MIN_ENTRIES)
    # Last-before-win can be sparse; show from 1 player.
    last_words = display_words(last_before_win, 1)
    histogram = _build_censorly_attempt_histogram(attempt_counts)
    solve_times = _completed_time_map(game, task.task_group, actors)
    return {
        'kind': 'censorly',
        'solved': len(actors),
        'summary': {
            'solved': len(actors),
            'median_attempts': _median(attempt_counts),
            'median_time_seconds': _median(list(solve_times.values())),
            'without_hints_percent': _pct(no_hints, len(actors)),
        },
        'distribution': histogram,
        RANKING_KEY: {'times': solve_times, 'attempts': actor_attempts, 'perfect': perfect},
        'popular_words': popular,
        'last_words': last_words,
    }


CRYPTIC_STATUSES = ('Ok', 'Partial', 'Wrong')
# A cryptic takes a few tries at most; the tail above this is one bar.
CRYPTIC_HISTOGRAM_TAIL = 10


def cryptic_answer_key(text):
    """Letters only, as the answer is checked: «Лесная  тропа» → «ЛЕСНАЯТРОПА»."""
    return re.sub(r'[^0-9A-ZА-ЯЁ]', '', str(text or '').upper())


def _cryptic_shown(text):
    return ' '.join(str(text or '').upper().split())


def _cryptic_guesses(rows):
    """Attempts through the first solve, the solving one included, and whether it solved.

    An advent «keep going» answer (Wrong with a state) is a step on the
    right track, not a guess: it is not counted.
    """
    taken = []
    for row in rows:
        if row.state == KEEP_GOING_STATE:
            continue
        taken.append(row)
        if row.status == 'Ok':
            return taken, True
    return taken, False


def single_answer_attempt_count(rows):
    """Guesses of a криптик or advent day as its statistics count them.

    Rows in time order; only real verdicts (no «keep going»), through the
    first right answer.
    None when the day is not solved. «Ваш результат» shows the same number.
    """
    taken, solved = _cryptic_guesses([row for row in rows if row.status in CRYPTIC_STATUSES])
    return len(taken) if solved else None


def _cryptic_hint_takers(task, game, actors):
    """Actor keys that took at least one of the task's hints."""
    from games.models import HintAttempt

    if not actors:
        return set()
    rows = HintAttempt.objects.filter(
        hint__task=task, is_real_request=True, replay_slot__isnull=True,
    )
    if len(actors) <= _ACTOR_OR_LIMIT:
        rows = rows.filter(_actor_q(actors))
    wanted = set(actors)
    return {
        _actor_key(row)
        for row in rows.only('team_id', 'user_id', 'anon_key')
        if _keeps_actor(row, wanted)
    }


def _cryptic(task, game, actors):
    """Криптик или адвент: попытки до решения, подсказки и популярные неверные ответы.

    ``kind`` is the game id, so the page tells the two apart. A task without
    hints (the advent) has no «Без подсказок» figure.
    """
    attempts = _attempts_for(task, game, actors, statuses=CRYPTIC_STATUSES)
    hint_takers = _cryptic_hint_takers(task, game, actors)
    answer = cryptic_answer_key(task.answer)
    attempt_counts = []
    actor_attempts = {}
    perfect = []
    wrong_players = defaultdict(set)
    wrong_forms = defaultdict(Counter)
    for actor in actors:
        rows, solved = _cryptic_guesses(attempts.get(actor, []))
        if not solved:
            continue
        n = len(rows)
        attempt_counts.append(n)
        actor_attempts[actor] = n
        if n == 1 and actor not in hint_takers:
            perfect.append(actor)
        for row in rows:
            key = cryptic_answer_key(row.text)
            if key and key != answer and row.status == 'Wrong':
                wrong_players[key].add(actor)
                wrong_forms[key][_cryptic_shown(row.text)] += 1
    total = len(actors)
    solve_times = _completed_time_map(game, task.task_group, actors)
    guesses = [
        {**item, 'word': wrong_forms[item['word']].most_common(1)[0][0]}
        for item in _popular_word_rows(wrong_players, limit=POPULAR_LIMIT)
    ]
    summary = {
        'solved': total,
        'median_attempts': _median(attempt_counts),
        'median_time_seconds': _median(list(solve_times.values())),
        'first_try_percent': _pct(sum(1 for n in attempt_counts if n == 1), total),
    }
    if task.get_hints():
        summary['without_hints_percent'] = _pct(total - len(hint_takers), total)
    return {
        'kind': game.id,
        'solved': total,
        'summary': summary,
        'distribution': build_attempt_histogram(attempt_counts, tail_from=CRYPTIC_HISTOGRAM_TAIL),
        RANKING_KEY: {'times': solve_times, 'attempts': actor_attempts, 'perfect': perfect},
        'guesses': guesses,
    }


def personal_cryptic_perfect(game, task_group, *, actor_key, attempts):
    """Same «perfect» rule as ``_cryptic``: one attempt and no hints."""
    if attempts != 1:
        return False
    task = Task.objects.filter(task_group=task_group).order_by('id').first()
    return task is not None and actor_key not in _cryptic_hint_takers(task, game, [actor_key])


DAILY_STATISTICS_ADAPTERS.update({
    'salad': DailyStatisticsAdapter('salad', _salad),
    'ladder': DailyStatisticsAdapter('ladder', _ladder),
    'alphabet': DailyStatisticsAdapter('alphabet', _alphabet),
    'censorly': DailyStatisticsAdapter('censorly', _censorly),
    'cryptic': DailyStatisticsAdapter('cryptic', _cryptic),
})


def build_daily_statistics(game, task_group, *, published_at=None, require_published=False):
    """Current statistics generation, shared through the cache.

    Page requests may get the previous generation while another process
    builds the current one. ``require_published`` (the background refresh)
    never takes that stale read: it waits for the current generation and,
    if the builder does not finish in time, caches its own calculation.
    """
    cache = caches['daily_statistics']
    revision_key = _cache_revision_key(game.id, task_group.id)
    result = None
    for _attempt in range(3):
        try:
            revision = int(cache.get(revision_key, 0) or 0)
            result_key = _versioned_cache_key(
                game.id, task_group.id, revision, published_at,
            )
            cached = cache.get(result_key)
        except Exception:
            logger.warning(
                'daily_statistics_cache_read_failed game=%s task_group=%s',
                game.id, task_group.id, exc_info=True,
            )
            return _build_daily_statistics_observed(game, task_group, published_at=published_at)
        if cached is not None:
            try:
                if int(cache.get(revision_key, 0) or 0) == revision:
                    return cached
            except Exception:
                logger.warning(
                    'daily_statistics_cache_revision_read_failed game=%s task_group=%s',
                    game.id, task_group.id, exc_info=True,
                )
                return _build_daily_statistics_observed(game, task_group, published_at=published_at)
            continue

        if not require_published:
            try:
                stale = _latest_stale_payload(
                    cache, game.id, task_group.id, revision, published_at,
                )
            except Exception:
                stale = None
                logger.warning(
                    'daily_statistics_stale_read_failed game=%s task_group=%s',
                    game.id, task_group.id, exc_info=True,
                )
            if stale is not None:
                return stale

        lock_key = '{}:build-lock'.format(result_key)
        lock_token = uuid.uuid4().hex
        try:
            owns_lock = cache.add(
                lock_key, lock_token, timeout=CACHE_BUILD_LOCK_TIMEOUT,
            )
        except Exception:
            logger.warning(
                'daily_statistics_cache_lock_failed game=%s task_group=%s',
                game.id, task_group.id, exc_info=True,
            )
            return _build_daily_statistics_observed(
                game, task_group, published_at=published_at,
            )

        if not owns_lock:
            revision_changed = False
            cache_failed = False
            for _ in range(int(CACHE_BUILD_WAIT_SECONDS / CACHE_BUILD_POLL_INTERVAL)):
                time.sleep(CACHE_BUILD_POLL_INTERVAL)
                try:
                    current_revision = int(cache.get(revision_key, 0) or 0)
                    if current_revision != revision:
                        revision_changed = True
                        break
                    cached = cache.get(result_key)
                    if cached is not None:
                        if int(cache.get(revision_key, 0) or 0) == revision:
                            return cached
                    if not require_published:
                        stale = _latest_stale_payload(
                            cache, game.id, task_group.id, revision, published_at,
                        )
                        if stale is not None:
                            return stale
                except Exception:
                    cache_failed = True
                    logger.warning(
                        'daily_statistics_cache_wait_failed game=%s task_group=%s',
                        game.id, task_group.id, exc_info=True,
                    )
                    break

            if revision_changed:
                continue

            if not cache_failed:
                try:
                    current_revision = int(cache.get(revision_key, 0) or 0)
                    if current_revision != revision:
                        continue
                    owns_lock = cache.add(
                        lock_key, lock_token, timeout=CACHE_BUILD_LOCK_TIMEOUT,
                    )
                except Exception:
                    owns_lock = False
                    logger.warning(
                        'daily_statistics_cache_lock_retry_failed game=%s task_group=%s',
                        game.id, task_group.id, exc_info=True,
                    )

            if not owns_lock:
                result = _build_daily_statistics_observed(
                    game, task_group, published_at=published_at,
                )
                try:
                    if int(cache.get(revision_key, 0) or 0) != revision:
                        if require_published:
                            continue
                        # A page shows what it built rather than starting over.
                        _store_generation(
                            cache, game.id, task_group.id, revision, published_at, result,
                        )
                        return result
                    if require_published and cache.get(result_key) is None:
                        _store_generation(
                            cache, game.id, task_group.id, revision, published_at, result,
                        )
                except Exception:
                    logger.warning(
                        'daily_statistics_cache_revision_read_failed game=%s task_group=%s',
                        game.id, task_group.id, exc_info=True,
                    )
                return result

        try:
            result = _build_daily_statistics_observed(
                game, task_group, published_at=published_at,
            )

            # If a completion invalidated this generation during the calculation,
            # rebuild against the newer participant set instead of caching/returning
            # a result that was already obsolete when the request finished.
            try:
                current_revision = int(cache.get(revision_key, 0) or 0)
                if current_revision != revision and require_published:
                    continue
                # A page request returns what it built even if a completion
                # landed meanwhile; the queued refresh builds the newer one.
                _store_generation(
                    cache, game.id, task_group.id, revision, published_at, result,
                )
            except Exception:
                logger.warning(
                    'daily_statistics_cache_write_failed game=%s task_group=%s',
                    game.id, task_group.id, exc_info=True,
                )
            return result
        finally:
            try:
                _release_build_lock(cache, lock_key, lock_token)
            except Exception:
                logger.warning(
                    'daily_statistics_cache_lock_release_failed game=%s task_group=%s',
                    game.id, task_group.id, exc_info=True,
                )

    # A burst of completions can keep advancing the revision. Return the last
    # fresh calculation without caching it; the next request will use the new
    # generation.
    return result if result is not None else _build_daily_statistics_observed(
        game, task_group, published_at=published_at,
    )


def _build_daily_statistics_payload(game, task_group, *, published_at=None):
    actors = _completed_actors(game, task_group, published_at=published_at)
    supported_task_types = tuple(
        definition.task_type
        for definition in DAILY_GAME_REGISTRY.all()
        if definition.capabilities.statistics and definition.statistics_adapter_key
    )
    if game.id in SINGLE_ANSWER_GAME_IDS:
        # A simple daily game outside the registry: one ordinary task a day.
        task = Task.objects.filter(task_group=task_group).order_by('id').first()
        if task is None:
            return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
        return DAILY_STATISTICS_ADAPTERS['cryptic'].build(task, game, actors)
    task = Task.objects.filter(
        task_group=task_group,
        task_type__in=supported_task_types,
    ).order_by('id').first()
    if task is None:
        return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
    adapter = get_daily_statistics_adapter(task.task_type)
    if adapter is None:
        return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
    return adapter.build(task, game, actors)
