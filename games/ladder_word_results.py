"""Per-ladder results: each middle raddle word is a column with hints 1 (clue) and 2 (answer)."""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db.models import F, Max, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    HintAttempt,
    PersonalResultsParticipant,
    Team,
    hidden_anon_keys,
)
from games.raddle import (
    load_raddle_state,
    parse_raddle_data,
    resolve_assist_tiers,
    word_solve_credit,
)
from games.results_sql_aggregate import _attempt_actor_specs


class _WordColHeader:
    __slots__ = ('number', 'name', '_n_tasks')

    def __init__(self, number):
        self.number = str(number)
        self.name = ''
        self._n_tasks = 1

    def get_n_tasks_for_results(self):
        return self._n_tasks


class _WordColTask:
    __slots__ = ('number',)

    def __init__(self, number):
        self.number = str(number)


def _to_float(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _hint_numbers_for_tier(tier):
    """Подсказки 1 (💡) и 2 (💡💡) для слова."""
    tier = int(tier or 0)
    if tier >= 2:
        return [1, 2]
    if tier >= 1:
        return [1]
    return []


def _load_chain_states_by_actor(task, game):
    """
    Map actor key → ChainTaskState.state for game_mode=general.
    Keys: ('team', team_id) | ('user', user_id) | ('anon', anon_key)
    """
    rows = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general',
    ).only('team_id', 'user_id', 'anon_key', 'state')
    out = {}
    for row in rows:
        if row.team_id:
            out[('team', row.team_id)] = row.state
        elif row.user_id:
            out[('user', row.user_id)] = row.state
        elif row.anon_key:
            out[('anon', row.anon_key)] = row.state
    return out


def _load_fallback_attempt_states(task, game, actor_keys):
    """
    Latest non-empty Attempt.state per actor (values only — no ORM hydrate of all attempts).
    Used when ChainTaskState is missing (legacy / tests).
    """
    if not actor_keys:
        return {}

    out = {}
    for kind, actor_field, actor_filter in _attempt_actor_specs():
        wanted = [raw for key_kind, raw in actor_keys if key_kind == kind]
        if not wanted:
            continue
        qs = (
            Attempt.manager.filter(task=task, game=game, skip=False)
            .filter(actor_filter, **{'{}__in'.format(actor_field): wanted})
            .exclude(Q(state__isnull=True) | Q(state=''))
            .annotate(
                rn=Window(
                    expression=RowNumber(),
                    partition_by=[F(actor_field)],
                    order_by=[F('time').desc()],
                ),
            )
            .filter(rn=1)
            .values(actor_field, 'state')
        )
        for row in qs:
            out[(kind, row[actor_field])] = row['state']
    return out


def _load_max_times_by_actor(task, game):
    out = {}
    base = Attempt.manager.filter(task=task, game=game, skip=False)
    for kind, actor_field, actor_filter in _attempt_actor_specs():
        rows = (
            base.filter(actor_filter)
            .values(actor_field)
            .annotate(max_time=Max('time'))
        )
        for row in rows:
            out[(kind, row[actor_field])] = row['max_time']
    return out


def _load_assist_hint_attempts_by_actor(task):
    """actor key → list of fake HintAttempt-like objects for resolve_assist_tiers."""
    rows = (
        HintAttempt.objects.filter(hint__task=task, is_real_request=True)
        .filter(
            Q(hint__desc__istartswith='raddle_clue:')
            | Q(hint__desc__istartswith='raddle_answer:')
        )
        .values('team_id', 'user_id', 'anon_key', 'hint__desc')
    )
    out = defaultdict(list)
    for row in rows:
        if row['team_id']:
            key = ('team', row['team_id'])
        elif row['user_id']:
            key = ('user', row['user_id'])
        elif row['anon_key']:
            key = ('anon', row['anon_key'])
        else:
            continue
        # resolve_assist_tiers only needs hint.desc + is_real_request
        out[key].append(SimpleNamespace(
            is_real_request=True,
            hint=SimpleNamespace(desc=row['hint__desc']),
        ))
    return out


def _resolve_participants(actor_keys):
    hidden_anons = hidden_anon_keys()
    team_ids = {raw for kind, raw in actor_keys if kind == 'team'}
    user_ids = {raw for kind, raw in actor_keys if kind == 'user'}
    teams = {t.pk: t for t in Team.objects.filter(pk__in=team_ids)} if team_ids else {}
    users = {u.pk: u for u in User.objects.filter(pk__in=user_ids)} if user_ids else {}
    participants = {}
    for key in actor_keys:
        kind, raw = key
        if kind == 'team':
            team = teams.get(raw)
            if team is None or team.is_hidden:
                continue
            participants[key] = team
        elif kind == 'user':
            user = users.get(raw)
            if user is None:
                continue
            participants[key] = PersonalResultsParticipant(user=user)
        else:
            if raw in hidden_anons:
                continue
            participants[key] = PersonalResultsParticipant(anon_key=raw)
    return participants


def build_ladder_word_results_context(game, placement, task):
    """
    Results context shaped like snapshot_to_results_context / results templates.

    Columns = middle words (indices 1..n-2). Cell points = word credit × task.points.
    hint_numbers = [1] and/or [2] from assist tier.

    Built from ChainTaskState + light SQL (max time, fallback Attempt.state, assist hints)
    — does not hydrate every Attempt ORM row.
    """
    parsed = parse_raddle_data(task)
    if not parsed:
        return {
            'task_groups': [],
            'task_group_to_tasks': {},
            'teams_sorted': [],
            'team_to_list_attempts_info': {},
            'team_to_cells': {},
            'team_to_score': {},
            'team_to_place': {},
            'team_to_max_best_time': {},
            'ladder_word_count': 0,
        }

    n_words = parsed['n_words']
    middle_indices = list(range(1, max(0, n_words - 1)))
    assist_cfg = parsed.get('assist')
    try:
        task_mul = _to_float(task.get_points() if hasattr(task, 'get_points') else 1)
    except Exception:
        task_mul = 1.0
    if task_mul <= 0:
        task_mul = 1.0

    max_word_points = _to_float(word_solve_credit(0, assist_cfg)) * task_mul

    task_groups = [_WordColHeader(i) for i in range(1, len(middle_indices) + 1)]
    task_group_to_tasks = {h.number: [_WordColTask(h.number)] for h in task_groups}

    chain_by_actor = _load_chain_states_by_actor(task, game)
    max_times = _load_max_times_by_actor(task, game)
    # Current games have ChainTaskState for active actors. Only legacy actors
    # without one need the expensive latest-state window query.
    fallback_states = _load_fallback_attempt_states(
        task, game, set(max_times) - set(chain_by_actor),
    )
    assist_hints = _load_assist_hint_attempts_by_actor(task)

    actor_keys = set(chain_by_actor) | set(fallback_states) | set(max_times) | set(assist_hints)
    participants = _resolve_participants(actor_keys)

    team_to_score = {}
    team_to_max_best_time = {}
    team_to_cells = {}
    team_to_list_attempts_info = {}

    for key, participant in participants.items():
        raw_state = chain_by_actor.get(key)
        if raw_state is None:
            raw_state = fallback_states.get(key)
        if raw_state is None and key not in assist_hints:
            # Attempts without state and without assists — nothing to show in word cols.
            continue

        state = load_raddle_state(raw_state, n_words)
        tiers = resolve_assist_tiers(state, assist_hints.get(key))

        cells = []
        score = 0.0
        for wi in middle_indices:
            tier = tiers.get(wi, 0)
            hint_numbers = _hint_numbers_for_tier(tier)
            solved = set(state.get('solved_indices') or [])
            if wi in solved:
                points = _to_float(word_solve_credit(tier, assist_cfg)) * task_mul
                if max_word_points > 0 and points >= max_word_points - 1e-9:
                    cls = 'cell-full'
                elif points <= 0:
                    cls = 'cell-zero'
                else:
                    cls = 'cell-partial'
                score += points
                n_attempts = 1
            else:
                points = 0.0
                n_attempts = 1 if hint_numbers else 0
                cls = 'cell-partial' if hint_numbers else ''

            cells.append({
                'cls': cls,
                'n_attempts': n_attempts,
                'result_points': points,
                'hint_numbers': hint_numbers,
            })

        # Skip empty rows (no solved middles and no assist markers).
        if score <= 0 and not any(c['hint_numbers'] or c['n_attempts'] for c in cells):
            if not (state.get('solved_indices') or assist_hints.get(key)):
                continue

        team_to_score[participant] = score
        team_to_cells[participant] = cells
        team_to_list_attempts_info[participant] = [None] * len(cells)

        best_time = max_times.get(key)
        if best_time is not None:
            team_to_max_best_time[participant] = best_time

    sort_rows = []
    now = timezone.now()
    for participant, score in team_to_score.items():
        t = team_to_max_best_time.get(participant) or now
        ts = t.timestamp() if hasattr(t, 'timestamp') else float('inf')
        sort_rows.append((-score, ts, participant))
    teams_sorted = [
        p for _s, _t, p in sorted(sort_rows, key=lambda row: (row[0], row[1], str(row[2])))
    ]

    team_to_place = {}
    for i, participant in enumerate(teams_sorted):
        team_to_place[participant] = 1 + i
        if i:
            prev = teams_sorted[i - 1]
            if team_to_score[participant] == team_to_score[prev]:
                team_to_place[participant] = team_to_place[prev]

    return {
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'teams_sorted': teams_sorted,
        'team_to_list_attempts_info': team_to_list_attempts_info,
        'team_to_cells': team_to_cells,
        'team_to_score': team_to_score,
        'team_to_place': team_to_place,
        'team_to_max_best_time': team_to_max_best_time,
        'ladder_word_count': len(middle_indices),
    }


def ladder_word_results_headers_context(task):
    parsed = parse_raddle_data(task)
    if not parsed:
        return {
            'task_groups': [],
            'task_group_to_tasks': {},
            'ladder_word_count': 0,
        }
    n_words = parsed['n_words']
    middle_indices = list(range(1, max(0, n_words - 1)))
    task_groups = [_WordColHeader(i) for i in range(1, len(middle_indices) + 1)]
    task_group_to_tasks = {h.number: [_WordColTask(h.number)] for h in task_groups}
    return {
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'ladder_word_count': len(middle_indices),
    }
