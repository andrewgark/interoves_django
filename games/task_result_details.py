"""State-backed details used when rendering task-level results."""

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.actor_state import (
    chain_state_for_result_actor,
    latest_attempt_state_for_result_actor,
)
from games.models import Attempt, ChainTaskState, GameTaskGroup, RaddleUiState
from games.word_salad import WORD_SALAD_GAME_ID


class SaladResultHeader:
    def __init__(self, number):
        self.number = str(number)

    def get_n_tasks_for_results(self):
        return 1


class SaladResultWord:
    def __init__(self, number, word):
        # ``number`` stays compatible with the legacy result context (answer),
        # while the public table header uses display_number.
        self.number = word
        self.display_number = str(number)
        self.answer = word
        self.display_answer = ''


def chain_state_for_actor(game, task, actor):
    return chain_state_for_result_actor(game, task, actor)


def raddle_ui_state_for_actor(
    game, task, *, team=None, user=None, anon_key=None,
    mode='general', replay_slot=None,
):
    if game is None or task is None:
        return None
    filters = {
        'task': task,
        'game': game,
        'game_mode': 'tournament' if mode == 'tournament' else 'general',
        'replay_slot': replay_slot,
        'replay_run_id': getattr(replay_slot, 'run_id', None),
    }
    if team is not None:
        filters['actor_key'] = 'team:{}'.format(team.pk)
        filters['namespace_key'] = (
            'replay:{}'.format(getattr(replay_slot, 'run_id', None))
            if replay_slot is not None else 'official'
        )
        filters.update(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        filters['actor_key'] = 'user:{}'.format(user.pk)
        filters['namespace_key'] = (
            'replay:{}'.format(getattr(replay_slot, 'run_id', None))
            if replay_slot is not None else 'official'
        )
        filters.update(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        filters['actor_key'] = 'anon:{}'.format(anon_key)
        filters['namespace_key'] = (
            'replay:{}'.format(getattr(replay_slot, 'run_id', None))
            if replay_slot is not None else 'official'
        )
        filters.update(anon_key=anon_key, team__isnull=True, user__isnull=True)
    else:
        return None
    return RaddleUiState.objects.filter(**filters).only(
        'drafts', 'clue_marks', 'revision',
    ).first()


def latest_actor_task_state(game, task, actor):
    chain_row = chain_state_for_actor(game, task, actor)
    if chain_row is not None and chain_row.state:
        return chain_row.state
    return latest_attempt_state_for_result_actor(game, task, actor)


def set_current_result_header_answers(data, actor, game=None):
    """Expose solved words in result headers only for the current actor."""
    if actor is None:
        return data
    cells_by_actor = data.get('team_to_cells') or {}
    cells = cells_by_actor.get(actor) or []
    if not cells:
        actor_kind = (
            'team' if getattr(actor, 'is_team_results_row', False)
            else 'user' if getattr(actor, 'user_id', None) is not None
            else 'anon'
        )
        actor_id = (
            getattr(actor, 'pk', None)
            if actor_kind == 'team'
            else getattr(actor, 'user_id', None) or getattr(actor, 'anon_key', None)
        )
        for candidate, candidate_cells in cells_by_actor.items():
            candidate_kind = (
                'team' if getattr(candidate, 'is_team_results_row', False)
                else 'user' if getattr(candidate, 'user_id', None) is not None
                else 'anon'
            )
            candidate_id = (
                getattr(candidate, 'pk', None)
                if candidate_kind == 'team'
                else getattr(candidate, 'user_id', None) or getattr(candidate, 'anon_key', None)
            )
            if actor_kind == candidate_kind and actor_id == candidate_id:
                cells = candidate_cells
                break

    tasks = [
        task
        for task_group in (data.get('task_groups') or [])
        for task in (data.get('task_group_to_tasks') or {}).get(task_group.number, [])
    ]
    salad_task = data.get('_salad_result_task')
    if game is not None and getattr(game, 'id', None) == WORD_SALAD_GAME_ID and salad_task is not None:
        from games.word_salad import load_state
        raw_state = latest_actor_task_state(game, salad_task, actor)
        solved_indices = set(load_state(raw_state).get('solved_indices') or [])
        for index, task in enumerate(tasks):
            answer = getattr(task, 'answer', '')
            if index in solved_indices and answer:
                task.display_answer = answer
        return data

    for index, task in enumerate(tasks):
        cell = cells[index] if index < len(cells) else {}
        solved = cell.get('solved')
        if not solved and game is not None and getattr(task, 'task_type', None) == 'alphabetty':
            from games.alphabetty.play import load_state
            raw_state = latest_actor_task_state(game, task, actor)
            solved = bool(raw_state and load_state(raw_state).get('won'))
        answer = cell.get('answer') or getattr(task, 'answer', '')
        if not answer and game is not None and getattr(game, 'id', None) == ALPHABETTY_GAME_ID:
            answer = (getattr(task, 'checker_data', '') or '').strip().splitlines()[0]
        if solved and answer:
            task.display_answer = answer
    return data


def word_salad_release_breakdown(data, game, number):
    """Expand Word Salad state into one result cell per word."""
    placement = GameTaskGroup.objects.filter(game=game, number=str(number)).select_related(
        'task_group',
    ).first()
    if not placement:
        return data
    tasks = list(placement.task_group.tasks.visible().filter(task_type='word_salad'))
    if not tasks:
        return data
    task = tasks[0]
    from games.word_salad import load_state, parse_task_payload
    try:
        _grid, words, _rare = parse_task_payload(task.checker_data, task.answer or task.text or '')
    except Exception:
        words = []
    data['task_groups'] = [SaladResultHeader(number)]
    data['task_group_to_tasks'] = {
        str(number): [SaladResultWord(index + 1, word) for index, word in enumerate(words)]
    }
    data['_salad_result_task'] = task
    chain_states = {}
    for row in ChainTaskState.objects.filter(
        task=task,
        game=game,
        game_mode='general',
        replay_slot__isnull=True,
    ).only('team_id', 'user_id', 'anon_key', 'state'):
        if row.team_id:
            key = ('team', row.team_id)
        elif row.user_id:
            key = ('user', row.user_id)
        elif row.anon_key:
            key = ('anon', row.anon_key)
        else:
            continue
        chain_states[key] = row.state

    attempt_states = {}
    for row in Attempt.manager.filter(
        task=task,
        game=game,
        replay_slot__isnull=True,
        skip=False,
    ).exclude(state__isnull=True).exclude(state='').order_by('time').only(
        'team_id', 'user_id', 'anon_key', 'state',
    ):
        key = (
            ('team', row.team_id) if row.team_id else
            ('user', row.user_id) if row.user_id else
            ('anon', row.anon_key)
        )
        if key[1] is not None:
            attempt_states[key] = row.state

    def actor_key(actor):
        if getattr(actor, 'is_team_results_row', False):
            return ('team', actor.pk)
        if getattr(actor, 'user_id', None) is not None:
            return ('user', actor.user_id)
        if getattr(actor, 'anon_key', None):
            return ('anon', actor.anon_key)
        return None

    cells_by_actor = {}
    infos_by_actor = {}
    for actor in data.get('teams_sorted', []):
        infos = data.get('team_to_list_attempts_info', {}).get(actor, [])
        info = infos[0] if infos else None
        attempts = getattr(info, 'attempts', None) or []
        raw_state = chain_states.get(actor_key(actor))
        if raw_state is None:
            raw_state = attempt_states.get(actor_key(actor))
        if raw_state is None:
            raw_state = attempts[-1].state if attempts else None
        state = load_state(raw_state)
        solved = set(state.get('solved_indices') or [])
        hint_counts = state.get('hint_counts') or {}
        cells = []
        for index, word in enumerate(words):
            hints = int(hint_counts.get(str(index), hint_counts.get(index, 0)) or 0)
            is_solved = index in solved
            net = (1.0 if is_solved else 0.0) - 0.5 * hints
            cells.append({
                'cls': 'cell-full' if is_solved and hints == 0 else 'cell-partial' if is_solved or hints else '',
                'n_attempts': 1 if is_solved or hints else 0,
                'result_points': net,
                'hint_numbers': list(range(1, hints + 1)),
                'number': index + 1,
                'answer': word,
                'solved': is_solved,
            })
        cells_by_actor[actor] = cells
        infos_by_actor[actor] = [info] * len(cells)
    data['team_to_cells'] = cells_by_actor
    data['team_to_list_attempts_info'] = infos_by_actor
    return data
