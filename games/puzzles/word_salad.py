"""Салатик: board payload and the fields of one submitted path or hint."""

from types import SimpleNamespace

from games.daily.share import get_daily_share_adapter
from games.daily.state import latest_daily_state
from games.puzzles.base import Puzzle
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
    include_task_html = staticmethod(include_task_html)
    results_max_points = staticmethod(results_max_points)
