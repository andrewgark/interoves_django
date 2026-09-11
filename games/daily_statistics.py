"""Aggregated post-completion statistics for the three daily games."""

import json
from collections import Counter, defaultdict
from statistics import median

from django.core.cache import cache
from django.db.models import Q

from games.models import Attempt, ChainTaskState, DailySolveTiming, PlayerCompletedGame, Task
from games.raddle import load_raddle_state, parse_raddle_data
from games.word_salad import load_state as load_salad_state, parse_task_payload
from games.alphabetty.core import normalize_word


CACHE_VERSION = 2
CACHE_TIMEOUT = 60 * 60 * 24


def cache_key(game_id, task_group_id):
    return 'daily-statistics:v{}:{}:{}'.format(CACHE_VERSION, game_id, task_group_id)


def invalidate_daily_statistics(game_id, task_group_id):
    if game_id and task_group_id:
        cache.delete(cache_key(game_id, task_group_id))


def _actor_key(row):
    if row.team_id:
        return ('team', row.team_id)
    if row.user_id:
        return ('user', row.user_id)
    return ('anon', str(row.anon_key))


def _median(values):
    return round(float(median(values)), 1) if values else None


def _pct(n, total):
    return round(100.0 * n / total, 1) if total else 0


def build_attempt_histogram(values, tail_from=30):
    """Build the fixed 1..29 plus 30+ alphabet attempt histogram."""
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


def _completed_actors(game, task_group):
    rows = PlayerCompletedGame.objects.filter(
        game=game, task_group=task_group,
        result=PlayerCompletedGame.RESULT_SOLVED,
    ).only('team_id', 'user_id', 'anon_key')
    return {_actor_key(row): row for row in rows}


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
        if kind == 'user':
            q |= Q(user_id=value, anon_key__isnull=True)
        elif kind == 'anon':
            q |= Q(anon_key=value, user__isnull=True)
    return q


def _attempts_for(task, game, actors):
    if not actors:
        return {}
    rows = Attempt.manager.filter(
        task=task, game=game, skip=False, status__in=('Ok', 'Partial'),
    ).filter(_actor_q(actors)).only(
        'id', 'team_id', 'user_id', 'anon_key', 'text', 'state', 'status',
        'time', 'active_time_ms',
    ).order_by('time', 'id')
    grouped = defaultdict(list)
    for row in rows:
        grouped[_actor_key(row)].append(row)
    return grouped


def _completed_times(game, task_group, actors):
    if not actors:
        return []
    rows = DailySolveTiming.objects.filter(
        game=game, task_group=task_group, status=DailySolveTiming.STATUS_COMPLETED,
    ).filter(_timing_actor_q(actors)).only('user_id', 'anon_key', 'frozen_ms', 'timing_version')
    values = {}
    for row in rows:
        if row.timing_version and row.frozen_ms is not None:
            values[_actor_key(row)] = int(row.frozen_ms) / 1000
    return list(values.values())


def _latest_states(task, game, actors, attempts):
    states = {}
    rows = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general',
    ).filter(_actor_q(actors)).only('team_id', 'user_id', 'anon_key', 'state')
    for row in rows:
        states[_actor_key(row)] = row.state
    for key, values in attempts.items():
        if key not in states:
            for row in reversed(values):
                if row.state:
                    states[key] = row.state
                    break
    return states


def _salad(task, game, actors):
    _grid, words, rare_words = parse_task_payload(task.checker_data, task.answer)
    attempts = _attempts_for(task, game, actors)
    states = _latest_states(task, game, actors, attempts)
    total = len(actors)
    no_hints = 0
    order = defaultdict(list)
    intervals = defaultdict(list)
    rare_counts = defaultdict(set)
    extra_counts = defaultdict(set)
    for actor in actors:
        state = load_salad_state(states.get(actor))
        hints = state.get('hint_counts') or {}
        if not hints:
            no_hints += 1
        solved_before = set()
        prev_active = None
        self_position = 0
        for row in attempts.get(actor, []):
            try:
                payload = json.loads(row.text or '{}')
                if (payload.get('action') or 'solve') != 'solve':
                    continue
                after = load_salad_state(row.state)
                added = set(after.get('solved_indices') or []) - solved_before
                for index in added:
                    # A hinted target is still counted in hint-rate, but is not
                    # a self-found order/time observation.
                    if int(hints.get(index, 0) or 0) <= 0 and row.active_time_ms is not None:
                        self_position += 1
                        order[index].append(self_position)
                        intervals[index].append(
                            int(row.active_time_ms) - (prev_active or 0)
                        )
                    prev_active = row.active_time_ms if row.active_time_ms is not None else prev_active
                solved_before |= added
            except (TypeError, ValueError):
                continue
        for word in state.get('found_rare_words') or []:
            rare_counts[normalize_word(word)].add(actor)
        for word in state.get('found_extra') or []:
            extra_counts[normalize_word(word)].add(actor)
    def side_rows(counts):
        return [
            {'word': word, 'players': len(players)}
            for word, players in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))[:10]
            if word
        ]
    return {
        'kind': 'salad', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(_completed_times(game, task.task_group, actors)), 'without_hints_percent': _pct(no_hints, total)},
        'words': [
            {'word': words[index], 'median_order': _median(order[index]), 'median_time_seconds': _median([v / 1000 for v in intervals[index]]), 'hint_percent': _pct(sum(1 for actor in actors if int((load_salad_state(states.get(actor)).get('hint_counts') or {}).get(index, 0) or 0) > 0), total)}
            for index in range(len(words))
        ],
        'rare': side_rows(rare_counts), 'off_topic': side_rows(extra_counts),
    }


def _ladder(task, game, actors):
    parsed = parse_raddle_data(task)
    attempts = _attempts_for(task, game, actors)
    total = len(actors)
    no_hints = 0
    times = defaultdict(list)
    for actor in actors:
        assist = {}
        previous = set()
        previous_active = None
        for row in attempts.get(actor, []):
            try:
                state = load_raddle_state(row.state, parsed['n_words'])
                current = set(state.get('solved_indices') or [])
                payload = json.loads(row.text or '{}')
                index = int(payload.get('word_index', -1))
            except (TypeError, ValueError):
                continue
            if index in current - previous:
                tier = int((state.get('assist_tier') or {}).get(str(index), 0) or 0)
                assist[index] = max(assist.get(index, 0), tier)
                if tier == 0 and row.active_time_ms is not None and index not in (0, parsed['n_words'] - 1):
                    times[index].append((int(row.active_time_ms) - (previous_active or 0)) / 1000)
                previous_active = row.active_time_ms if row.active_time_ms is not None else previous_active
            previous = current
        if not assist:
            no_hints += 1
        elif not any(assist.values()):
            no_hints += 1
    return {
        'kind': 'ladder', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(_completed_times(game, task.task_group, actors)), 'without_hints_percent': _pct(no_hints, total)},
        'words': [{'word': parsed['words'][i], 'given': i in (0, parsed['n_words'] - 1), 'median_time_seconds': _median(times[i])} for i in range(parsed['n_words'])],
    }


def _alphabet(task, game, actors):
    attempts = _attempts_for(task, game, actors)
    answer = normalize_word((task.answer or '').splitlines()[0])
    attempt_counts = []
    guesses = defaultdict(set)
    for actor in actors:
        rows = attempts.get(actor, [])
        won = [row for row in rows if row.status == 'Ok']
        if not won:
            continue
        n = len(rows)
        attempt_counts.append(n)
        for row in rows:
            guess = normalize_word(row.text)
            if guess and guess != answer and row.status != 'Ok':
                guesses[guess].add(actor)
    histogram = build_attempt_histogram(attempt_counts)
    return {'kind': 'alphabet', 'solved': len(actors), 'summary': {'solved': len(actors), 'median_attempts': _median(attempt_counts)}, 'distribution': histogram, 'guesses': [{'word': word, 'players': len(players)} for word, players in sorted(guesses.items(), key=lambda item: (-len(item[1]), item[0]))[:10]]}


def build_daily_statistics(game, task_group):
    cached = cache.get(cache_key(game.id, task_group.id))
    if cached is not None:
        return cached
    actors = _completed_actors(game, task_group)
    task = Task.objects.filter(task_group=task_group, task_type__in=('raddle', 'word_salad', 'alphabetty')).order_by('id').first()
    if task is None:
        return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
    if task.task_type == 'word_salad':
        result = _salad(task, game, actors)
    elif task.task_type == 'raddle':
        result = _ladder(task, game, actors)
    else:
        result = _alphabet(task, game, actors)
    cache.set(cache_key(game.id, task_group.id), result, CACHE_TIMEOUT)
    return result
