"""Game-specific board payload builders used by the shared daily view."""

from dataclasses import dataclass
from typing import Callable

from games.daily.registry import get_daily_game
from games.daily.share import get_daily_share_adapter
from games.daily.state import latest_daily_state
from games.raddle import load_raddle_state, parse_raddle_data
from games.raddle import build_raddle_ui_context
from games.word_salad import (
    build_ui_context as build_word_salad_ui_context,
    load_state as load_word_salad_state,
    parse_task_payload as parse_word_salad_task_payload,
)


@dataclass(frozen=True)
class DailyBoardAdapter:
    """Dispatch entry for one task type's board payload builder."""

    task_type: str
    build: Callable
    prepare: Callable
    context_key: str
    body_template: str | None = None
    body_wrapper: bool | None = None


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
    except Exception:
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
    state = latest_daily_state(
        task,
        game,
        attempts_info,
        default_state=load_raddle_state(None, parsed['n_words']),
        decode_state=lambda raw: load_raddle_state(raw, parsed['n_words']),
        resolve_chain_state=resolve_chain_state,
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
        attempts=(attempts_info.attempts if attempts_info else []),
        hint_attempts=(attempts_info.hint_attempts if attempts_info else []),
        mode=mode,
        ui_state=raddle_ui_state_for_actor(
            game,
            task,
            team=team,
            user=user,
            anon_key=anon_key,
            mode=mode,
            replay_slot=replay_slot,
        ),
        share_title=share_title,
        user=user,
        anon_key=anon_key,
    )


DAILY_BOARD_ADAPTERS = {
    adapter.task_type: adapter
    for adapter in (
        DailyBoardAdapter(
            'word_salad',
            build_word_salad_board_data,
            prepare_word_salad_board_data,
            'word_salad',
            'task-content/task-word-salad.html',
            True,
        ),
        DailyBoardAdapter(
            'raddle',
            build_raddle_board_data,
            prepare_raddle_board_data,
            'raddle',
            'task-content/task-raddle.html',
            True,
        ),
    )
}


def get_daily_board_adapter(task_type, *, game_id=None):
    """Return the registered board adapter, with task-type compatibility fallback."""
    definition = get_daily_game(game_id)
    adapter_key = (
        definition.board_adapter_key
        if definition is not None and definition.board_adapter_key
        else task_type
    )
    return DAILY_BOARD_ADAPTERS.get(str(adapter_key or ''))
