"""Aggregated post-completion statistics for the three daily games."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Callable

from django.core.cache import cache
from django.db.models import Q

from games.models import Attempt, ChainTaskState, DailySolveTiming, PlayerCompletedGame, Task
from games.raddle import load_raddle_state, parse_raddle_data, resolve_assist_tiers
from games.word_salad import (
    load_state as load_salad_state,
    longest_dictionary_words,
    parse_task_payload,
)
from games.alphabetty.core import normalize_word
from games.alphabetty.play import (
    hint_count as alphabetty_hint_count,
    load_state as load_alphabetty_state,
    secret_from_task,
    split_ladder,
)
from games.daily.registry import DAILY_GAME_REGISTRY


CACHE_VERSION = 16
CACHE_TIMEOUT = 10 * 60
POPULAR_LIMIT = 20
POPULAR_MIN_ENTRIES = 5
BOUND_WORDS_LIMIT = 10


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


def invalidate_daily_statistics(game_id, task_group_id):
    if game_id and task_group_id:
        cache.delete(cache_key(game_id, task_group_id))


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


def _completed_actors(game, task_group):
    rows = list(PlayerCompletedGame.objects.filter(
        game=game, task_group=task_group,
        result=PlayerCompletedGame.RESULT_SOLVED,
    ).select_related('team').only('team_id', 'user_id', 'anon_key', 'team'))
    from games.leaderboard import actor_key, eligible_public_actors

    actors = [actor for actor in (_results_actor(row) for row in rows) if actor is not None]
    eligible = {
        actor_key(actor)
        for actor in eligible_public_actors(actors, task_group=task_group)
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
        replay_slot__isnull=True,
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
        replay_slot__isnull=True,
    ).filter(_timing_actor_q(actors)).only('user_id', 'anon_key', 'frozen_ms', 'timing_version')
    values = {}
    for row in rows:
        if row.timing_version and row.frozen_ms is not None:
            values[_actor_key(row)] = int(row.frozen_ms) / 1000
    return list(values.values())


def _latest_states(task, game, actors, attempts):
    states = {}
    rows = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general', replay_slot__isnull=True,
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
    grid, words, rare_words = parse_task_payload(task.checker_data, task.answer)
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
            findings[word] = {'players': set(players), 'rare': True}
    for word, players in extra_counts.items():
        if word:
            finding = findings.setdefault(word, {'players': set(), 'rare': False})
            finding['players'].update(players)
    popular_findings = [
        {'word': word, 'players': len(item['players']), 'rare': item['rare']}
        for word, item in sorted(findings.items(), key=lambda entry: (-len(entry[1]['players']), entry[0]))
        if len(item['players']) >= POPULAR_MIN_ENTRIES
    ][:POPULAR_LIMIT]
    rare_rows = [
        {'word': word, 'players': len(players), 'rare': True}
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
            found_words[word] = {'word': word, 'kind': 'rare'}
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
        tuple(grid), tuple(normalize_word(word) for word in words + rare_words),
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
    return {
        'kind': 'salad', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(_completed_times(game, task.task_group, actors)), 'without_hints_percent': _pct(no_hints, total)},
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
    for actor in actors:
        assist = {}
        previous = set()
        previous_active = 0
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
            previous = current
        if not assist:
            no_hints += 1
        elif not any(assist.values()):
            no_hints += 1
        for index, tier in assist.items():
            if tier > 0:
                hint_counts[index] += 1
    return {
        'kind': 'ladder', 'solved': total,
        'summary': {'solved': total, 'median_time_seconds': _median(_completed_times(game, task.task_group, actors)), 'without_hints_percent': _pct(no_hints, total)},
        'word_stats_available': all(times[index] for index in range(1, parsed['n_words'] - 1)),
        'words': [
            {
                'word': parsed['words'][i],
                'given': i in (0, parsed['n_words'] - 1),
                'median_time_seconds': _median(times[i]),
                'hint_percent': _pct(hint_counts[i], total),
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
    return {
        'kind': 'alphabet',
        'solved': len(actors),
        'summary': {
            'solved': len(actors),
            'median_attempts': _median(attempt_counts),
            'median_time_seconds': _median(_completed_times(game, task.task_group, actors)),
            'without_hints_percent': _pct(no_hints, len(actors)),
        },
        'distribution': histogram,
        'guesses': popular_guesses,
        'above_words': above_words,
        'below_words': below_words,
    }


def _censorly(task, game, actors):
    """Popular guessed lemmas + last lemma before win (excluding title answers)."""
    from games.censorly.normalize import lemma_of, normalize_surface
    from games.censorly.play import hint_count as censorly_hint_count
    from games.censorly.play import load_state as load_censorly_state
    from games.censorly.play import puzzle_from_task
    from games.censorly.tokenize import title_content_lemmas

    attempts = _attempts_for(task, game, actors)
    states = _latest_states(task, game, actors, attempts)
    payload = puzzle_from_task(task) or {}
    title_lemmas = title_content_lemmas(payload)
    attempt_counts = []
    lemma_players = defaultdict(set)
    last_before_win = defaultdict(set)
    no_hints = 0

    def lemma_from_row(row):
        try:
            dumped = json.loads(row.state or '{}')
            guesses = dumped.get('guesses') or []
            if guesses:
                lemma = (guesses[-1] or {}).get('lemma') or ''
                if lemma:
                    return lemma
        except (TypeError, ValueError):
            pass
        return lemma_of(normalize_surface(row.text or ''))

    for actor in actors:
        rows = attempts.get(actor, [])
        if not any(row.status == 'Ok' for row in rows):
            continue
        real_guesses = [
            row for row in rows
            if row.text and not str(row.text).startswith('#hint:')
        ]
        attempt_counts.append(len(real_guesses))
        state = load_censorly_state(states.get(actor))
        if censorly_hint_count(state) == 0:
            no_hints += 1
        for row in real_guesses:
            lemma = lemma_from_row(row)
            if lemma and lemma not in title_lemmas:
                lemma_players[lemma].add(actor)
        pre_win = []
        for row in rows:
            if row.status == 'Ok':
                break
            if row.text and not str(row.text).startswith('#hint:'):
                pre_win.append(row)
        if pre_win:
            lemma = lemma_from_row(pre_win[-1])
            if lemma and lemma not in title_lemmas:
                last_before_win[lemma].add(actor)

    popular = [
        {'word': word, 'players': len(players)}
        for word, players in sorted(lemma_players.items(), key=lambda item: (-len(item[1]), item[0]))
        if len(players) >= POPULAR_MIN_ENTRIES
    ][:POPULAR_LIMIT]
    # Last-before-win can be sparse; show from 1 player.
    last_words = [
        {'word': word, 'players': len(players)}
        for word, players in sorted(last_before_win.items(), key=lambda item: (-len(item[1]), item[0]))
        if len(players) >= 1
    ][:POPULAR_LIMIT]
    histogram = build_attempt_histogram(attempt_counts)
    return {
        'kind': 'censorly',
        'solved': len(actors),
        'summary': {
            'solved': len(actors),
            'median_attempts': _median(attempt_counts),
            'median_time_seconds': _median(_completed_times(game, task.task_group, actors)),
            'without_hints_percent': _pct(no_hints, len(actors)),
        },
        'distribution': histogram,
        'popular_words': popular,
        'last_words': last_words,
    }


DAILY_STATISTICS_ADAPTERS.update({
    'salad': DailyStatisticsAdapter('salad', _salad),
    'ladder': DailyStatisticsAdapter('ladder', _ladder),
    'alphabet': DailyStatisticsAdapter('alphabet', _alphabet),
    'censorly': DailyStatisticsAdapter('censorly', _censorly),
})


def build_daily_statistics(game, task_group):
    cached = cache.get(cache_key(game.id, task_group.id))
    if cached is not None:
        return cached
    actors = _completed_actors(game, task_group)
    supported_task_types = tuple(
        definition.task_type
        for definition in DAILY_GAME_REGISTRY.all()
        if definition.capabilities.statistics and definition.statistics_adapter_key
    )
    task = Task.objects.filter(task_group=task_group, task_type__in=supported_task_types).order_by('id').first()
    if task is None:
        return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
    adapter = get_daily_statistics_adapter(task.task_type)
    if adapter is None:
        return {'kind': None, 'solved': len(actors), 'summary': {'solved': len(actors)}}
    result = adapter.build(task, game, actors)
    cache.set(cache_key(game.id, task_group.id), result, CACHE_TIMEOUT)
    return result
