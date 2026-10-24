"""Салатик: board payload and the fields of one submitted path or hint."""

import json
from types import SimpleNamespace

from games.daily.share import get_daily_share_adapter
from games.daily.state import latest_daily_state
from games.puzzles.base import Puzzle
from games.puzzles.progress import _int_tuple, _text_tuple
from games.puzzles.word_salad_board import load_salad_board
from games.puzzles.word_salad_submission import (
    attempt_limit_message,
    blocks_duplicate,
    include_task_html,
    parse_move,
    write_move_result,
)
from games.word_salad import (
    build_ui_context as build_word_salad_ui_context,
    load_state as load_word_salad_state,
    parse_task_data,
    parse_task_payload as parse_word_salad_task_payload,
    validate_task_data,
)


def build_word_salad_board_data(
    *,
    game,
    task,
    placement,
    grid,
    words,
    rare_words,
    state,
    attempts,
    user,
    anon_key,
):
    """Build Salad's board UI and attach its optional share-card payload."""
    ui = build_word_salad_ui_context(
        grid,
        words,
        state,
        attempts=attempts,
        rare_words=rare_words,
    )
    share_adapter = get_daily_share_adapter(game.id)
    if share_adapter is not None:
        share_adapter.attach(
            ui,
            words=words,
            grid=grid,
            state=state,
            game=game,
            task=task,
            placement=placement,
            user=user,
            anon_key=anon_key,
            attempts=attempts,
        )
    return ui


def prepare_word_salad_board_data(
    *,
    game,
    task,
    placement,
    attempts_info,
    team,
    user,
    anon_key,
    mode,
    replay_slot,
    resolve_chain_state,
    **ignored,
):
    """Parse, resolve, and build one Salad board payload."""
    try:
        grid, words, rare_words = parse_word_salad_task_payload(
            task.checker_data,
            task.answer,
        )
    except (TypeError, ValueError):
        return None
    if attempts_info is None:
        preloaded_attempts = None
    else:
        preloaded_attempts = attempts_info.attempts
    board = load_salad_board(
        game=game,
        task=task,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts=preloaded_attempts,
    )
    state_source = attempts_info if attempts_info is not None else SimpleNamespace(
        attempts=board.attempts,
    )
    state = latest_daily_state(
        task,
        game,
        state_source,
        default_state=load_word_salad_state(None),
        decode_state=load_word_salad_state,
        resolve_chain_state=lambda task, game, **kwargs: board.chain,
        chain_state_kwargs={
            'team': team,
            'user': user,
            'anon_key': anon_key,
            'mode': mode,
            'replay_slot': replay_slot,
        },
    )
    return build_word_salad_board_data(
        game=game,
        task=task,
        placement=placement,
        grid=grid,
        words=words,
        rare_words=rare_words,
        state=state,
        attempts=board.attempts,
        user=user,
        anon_key=anon_key,
    )


def progress_signature(state):
    """Solved words, rare words, and extras. Hints are not part of the signature."""
    return (
        'salad',
        _int_tuple(state.get('solved_indices')),
        _int_tuple(state.get('found_rare')),
        _text_tuple(state.get('found_extra')),
    )


def clean_checker_data(task):
    """Reject a salad payload that is not a playable grid."""
    validate_task_data(task.checker_data, '')


def is_complete(task, state_raw):
    """True when every salad word is solved."""
    if not state_raw:
        return False
    try:
        from games.word_salad import load_state
        _grid, words = parse_task_data(task.checker_data, task.answer)
        solved = set(load_state(state_raw).get('solved_indices') or [])
        return bool(words) and set(range(len(words))).issubset(solved)
    except Exception:
        return False


def scale_points(task, raw_points):
    """Salad points are already on the result scale. The task multiplier is not applied."""
    from decimal import Decimal
    del task
    return Decimal(str(raw_points or 0))


def pretty_text(attempt):
    """List label for a salad path or hint."""
    try:
        payload = json.loads(attempt.text)
        action = (payload.get('action') or 'solve').strip().lower()
        if action == 'hint':
            idx = int(payload.get('word_index', -1))
            if idx >= 0:
                return 'Подсказка слова {}'.format(idx + 1)
            return 'Подсказка'
        path = payload.get('path') or []
        if not isinstance(path, list):
            return attempt.text
        try:
            path = [int(value) for value in path]
        except (TypeError, ValueError):
            return attempt.text
        letters = ''
        try:
            grid, _words = parse_task_data(attempt.task.checker_data, attempt.task.answer or '')
            letters = ''.join(grid[i] for i in path if 0 <= i < len(grid))
        except Exception:
            letters = ''
        if letters:
            return letters
        if path:
            return 'Путь: {}'.format('-'.join(str(i) for i in path))
        return attempt.text
    except (ValueError, TypeError, AttributeError):
        return attempt.text


def capture_before_save(task):
    """The checker payload from before this save, used to detect a new grid."""
    return type(task).objects.filter(pk=task.pk).values_list(
        'checker_data', 'task_type',
    ).first()


def _grid_changed(task, previous):
    """Return whether this save changes the Word Salad 4x4 grid."""
    if previous is None or previous[1] != 'word_salad':
        return True
    try:
        old_grid, _old_words, _old_rare = parse_word_salad_task_payload(previous[0], '')
        new_grid, _new_words, _new_rare = parse_word_salad_task_payload(task.checker_data, '')
    except (TypeError, ValueError, json.JSONDecodeError):
        # A malformed/legacy payload is unsafe to treat as the same grid.
        return True
    return old_grid != new_grid


def after_save(task, *, is_existing, previous):
    """A changed grid starts a new chain. Answer-only edits keep the old chain."""
    if not is_existing or not _grid_changed(task, previous):
        return
    from games.models import ChainTaskState
    from games.word_salad import default_state, dump_state
    ChainTaskState.objects.filter(task=task).update(
        # A non-null empty state is intentional: check_attempt falls
        # back to historical Attempt.state only when the projection is
        # null (used for legacy anon-key migrations).
        state=dump_state(default_state()),
        last_attempt=None,
    )


def results_max_points(task, m):
    """Green-cell maximum: the word count. The task multiplier is not applied."""
    del m
    try:
        _, words = parse_task_data(task.checker_data, '')
        if words:
            return len(words)
    except Exception:
        pass
    return 0


def attempt_advanced(attempt, last_state):
    """True when this answer added a solved word. Bad JSON does not count."""
    try:
        before = json.loads(last_state or '{}')
        after = json.loads(getattr(attempt, 'state', None) or '{}')
    except (TypeError, ValueError):
        return False
    return bool(
        set(after.get('solved_indices') or [])
        - set(before.get('solved_indices') or [])
    )


def write_response(response):
    """Copy the salad verdict onto the JSON body. The view still owns HTML."""
    return write_move_result(
        response.result,
        response.attempt.task,
        response.attempt,
        attempt_persisted=response.attempt_persisted,
        user=response.user,
        anon_key=response.anon_key,
    )


class WordSaladPuzzle(Puzzle):
    """Board, response contract, and text duplicates that name the previous attempt."""

    task_type = 'word_salad'
    checker_id = None

    build_board = staticmethod(build_word_salad_board_data)
    prepare_board = staticmethod(prepare_word_salad_board_data)
    load_board = staticmethod(load_salad_board)
    parse_move = staticmethod(parse_move)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    write_move_result = staticmethod(write_move_result)
    write_response = staticmethod(write_response)
    include_task_html = staticmethod(include_task_html)
    progress_signature = staticmethod(progress_signature)
    clean_checker_data = staticmethod(clean_checker_data)
    scale_points = staticmethod(scale_points)
    is_complete = staticmethod(is_complete)
    pretty_text = staticmethod(pretty_text)
    capture_before_save = staticmethod(capture_before_save)
    after_save = staticmethod(after_save)
    attempt_advanced = staticmethod(attempt_advanced)
    results_max_points = staticmethod(results_max_points)
