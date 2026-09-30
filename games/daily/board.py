"""Game-specific board payload builders used by the shared daily view."""

from games.daily.share_card import attach_ladder_share_card, attach_salad_share_card
from games.raddle import build_raddle_ui_context
from games.word_salad import WORD_SALAD_GAME_ID, build_ui_context as build_word_salad_ui_context


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
    if str(getattr(game, 'id', '')) == WORD_SALAD_GAME_ID:
        attach_salad_share_card(
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
    attach_ladder_share_card(
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
