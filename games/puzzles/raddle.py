"""Лесенка: board payload and the rules of one submitted word."""

from games.daily.share import get_daily_share_adapter
from games.daily.state import latest_daily_state
from games.puzzles.base import Puzzle
from games.puzzles.raddle_board import load_ladder_board
from games.puzzles.raddle_submission import (
    accept_ui_draft,
    assist_block,
    attempt_limit_message,
    blocks_duplicate,
    duplicate_fields,
    include_task_html,
    parse_move,
    posted_word_index,
    stale_payload,
    write_move_result,
)
from games.raddle import (
    build_raddle_ui_context,
    load_raddle_state,
    parse_raddle_data,
)


def results_max_points(task, m):
    """Green-cell maximum: every word except the two ends, times the multiplier."""
    parsed = parse_raddle_data(task)
    if parsed:
        middle = max(0, parsed['n_words'] - 2)
        if middle > 0:
            return m * middle
    return m


def build_raddle_board_data(
    *,
    game,
    task,
    placement,
    parsed,
    state,
    attempts,
    hint_attempts,
    mode,
    ui_state,
    share_title,
    user,
    anon_key,
):
    """Build Raddle's board payload and attach its share-card payload."""
    if ui_state is not None:
        state['drafts'] = dict(ui_state.drafts or {})
        state['clue_marks'] = dict(ui_state.clue_marks or {})
    ui = build_raddle_ui_context(
        parsed,
        state,
        attempts,
        max_attempts=task.get_max_attempts(),
        mode=mode,
        hint_attempts=hint_attempts,
    )
    get_daily_share_adapter(game.id, fallback_key='ladder').attach(
        ui,
        parsed=parsed,
        state=state,
        hint_attempts=hint_attempts,
        game=game,
        task=task,
        placement=placement,
        user=user,
        anon_key=anon_key,
        attempts=attempts,
        share_title=share_title,
    )
    return {
        'parsed': parsed,
        'ui': ui,
        'max_attempts': task.get_max_attempts(),
        'max_points_total': task.get_results_max_points(),
    }


def prepare_raddle_board_data(
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
    raddle_ui_state_for_actor,
    share_title,
    **ignored,
):
    """Parse, resolve, and build one Raddle board payload."""
    parsed = parse_raddle_data(task)
    if not parsed:
        return None
    if attempts_info is None:
        preloaded_attempts = None
        preloaded_hints = None
    else:
        preloaded_attempts = attempts_info.attempts
        preloaded_hints = attempts_info.hint_attempts
    board = load_ladder_board(
        game=game,
        task=task,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts=preloaded_attempts,
        hint_attempts=preloaded_hints,
    )
    state = latest_daily_state(
        task,
        game,
        attempts_info,
        default_state=load_raddle_state(None, parsed['n_words']),
        decode_state=lambda raw: load_raddle_state(raw, parsed['n_words']),
        resolve_chain_state=lambda task, game, **kwargs: board.chain,
        chain_state_kwargs={
            'team': team,
            'user': user,
            'anon_key': anon_key,
            'mode': mode,
            'replay_slot': replay_slot,
        },
    )
    return build_raddle_board_data(
        game=game,
        task=task,
        placement=placement,
        parsed=parsed,
        state=state,
        attempts=board.attempts,
        hint_attempts=board.hint_attempts,
        mode=mode,
        ui_state=board.ui_state,
        share_title=share_title,
        user=user,
        anon_key=anon_key,
    )


class RaddlePuzzle(Puzzle):
    task_type = 'raddle'
    checker_id = 'raddle'

    build_board = staticmethod(build_raddle_board_data)
    prepare_board = staticmethod(prepare_raddle_board_data)
    parse_move = staticmethod(parse_move)
    load_board = staticmethod(load_ladder_board)
    assist_block = staticmethod(assist_block)
    accept_ui_draft = staticmethod(accept_ui_draft)
    posted_word_index = staticmethod(posted_word_index)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    stale_payload = staticmethod(stale_payload)
    duplicate_fields = staticmethod(duplicate_fields)
    write_move_result = staticmethod(write_move_result)
    include_task_html = staticmethod(include_task_html)
    results_max_points = staticmethod(results_max_points)
