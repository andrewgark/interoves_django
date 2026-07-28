"""Per-ladder results: each middle raddle word is a column with hints 1 (clue) and 2 (answer)."""

from __future__ import annotations

from django.utils import timezone

from games.models import Attempt, ChainTaskState, PersonalResultsParticipant, Team
from games.raddle import (
    load_raddle_state,
    parse_raddle_data,
    resolve_assist_tiers,
    word_solve_credit,
)


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


def _state_from_attempts_info(ai, n_words):
    state = load_raddle_state(None, n_words)
    for a in reversed(ai.attempts or []):
        if getattr(a, 'state', None):
            return load_raddle_state(a.state, n_words)
    return state


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


def _actor_key(participant):
    if isinstance(participant, Team):
        return ('team', participant.pk)
    if isinstance(participant, PersonalResultsParticipant):
        if participant.user_id is not None:
            return ('user', participant.user_id)
        if participant.anon_key:
            return ('anon', participant.anon_key)
    return None


def build_ladder_word_results_context(game, placement, task):
    """
    Results context shaped like snapshot_to_results_context / results templates.

    Columns = middle words (indices 1..n-2). Cell points = word credit × task.points.
    hint_numbers = [1] and/or [2] from assist tier.
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
    actor_rows = Attempt.manager.get_general_results_task_actor_rows(task=task, game=game)

    team_to_score = {}
    team_to_max_best_time = {}
    team_to_cells = {}
    team_to_list_attempts_info = {}

    for participant, ai in actor_rows:
        if not (ai.attempts or ai.hint_attempts):
            continue

        key = _actor_key(participant)
        raw_state = chain_by_actor.get(key) if key else None
        if raw_state:
            state = load_raddle_state(raw_state, n_words)
        else:
            state = _state_from_attempts_info(ai, n_words)

        solved = set(state.get('solved_indices') or [])
        tiers = resolve_assist_tiers(state, ai.hint_attempts)

        cells = []
        score = 0.0
        for wi in middle_indices:
            tier = tiers.get(wi, 0)
            hint_numbers = _hint_numbers_for_tier(tier)
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

        team_to_score[participant] = score
        team_to_cells[participant] = cells
        team_to_list_attempts_info[participant] = [None] * len(cells)

        best_time = None
        if ai.best_attempt is not None and getattr(ai.best_attempt, 'time', None):
            best_time = ai.best_attempt.time
        elif ai.attempts:
            try:
                best_time = max(a.time for a in ai.attempts if a.time)
            except ValueError:
                best_time = None
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
    """Headers only (progressive first paint)."""
    parsed = parse_raddle_data(task)
    if not parsed:
        return {
            'task_groups': [],
            'task_group_to_tasks': {},
            'ladder_word_count': 0,
        }
    n_words = parsed['n_words']
    middle_count = max(0, n_words - 2)
    task_groups = [_WordColHeader(i) for i in range(1, middle_count + 1)]
    task_group_to_tasks = {h.number: [_WordColTask(h.number)] for h in task_groups}
    return {
        'task_groups': task_groups,
        'task_group_to_tasks': task_group_to_tasks,
        'ladder_word_count': middle_count,
    }
