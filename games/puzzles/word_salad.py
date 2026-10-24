"""Салатик: board payload and the fields of one submitted path or hint."""

from games.daily.share import get_daily_share_adapter
from games.daily.state import latest_daily_state
from games.puzzles.base import Puzzle
from games.puzzles.word_salad_submission import (
    include_task_html,
    parse_move,
    write_move_result,
)
from games.word_salad import (
    build_ui_context as build_word_salad_ui_context,
    load_state as load_word_salad_state,
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
    state = latest_daily_state(
        task,
        game,
        attempts_info,
        default_state=load_word_salad_state(None),
        decode_state=load_word_salad_state,
        resolve_chain_state=resolve_chain_state,
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
        attempts=(attempts_info.attempts if attempts_info else []),
        user=user,
        anon_key=anon_key,
    )


class WordSaladPuzzle(Puzzle):
    """Board and response contract. Duplicate checks stay generic until this class owns them."""

    task_type = 'word_salad'

    build_board = staticmethod(build_word_salad_board_data)
    prepare_board = staticmethod(prepare_word_salad_board_data)
    parse_move = staticmethod(parse_move)
    write_move_result = staticmethod(write_move_result)
    include_task_html = staticmethod(include_task_html)
