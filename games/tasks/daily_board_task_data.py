"""Daily board adapters (raddle, word_salad, …) for task-group UI."""

from games.daily.catalog.ladder import LADDER_GAME_ID
from games.daily.page.board import get_daily_board_adapter
from games.daily.page.section import WORD_SALAD_GAME_ID
from games.tasks.actor_state import chain_state_for_actor
from games.tasks.titles import raddle_share_title


def _word_salad_offer_share_hash(game, task_group) -> str:
    if game.id != WORD_SALAD_GAME_ID:
        return ''
    from games.models import WordSaladOffer

    offer = (
        WordSaladOffer.objects
        .filter(task_group_id=task_group.pk)
        .only('share_hash')
        .first()
    )
    return offer.share_hash if offer is not None else ''


def daily_board_task_data(
    tasks,
    attempts_info_by_task_id,
    *,
    game,
    task_group,
    placement,
    team,
    user,
    anon_key,
    mode,
    replay_slot=None,
    raddle_ui_state_for_actor,
) -> dict:
    """
    Prepare daily-board payloads and per-task adapter metadata.

    Returns keys used by ``build_task_group_task_context_dicts``:
    ``board_data_by_context``, ``daily_board_*_by_task_id``.
    """
    daily_board_context_key_by_task_id = {}
    daily_board_template_by_task_id = {}
    daily_board_wrapper_by_task_id = {}
    daily_board_adapter_by_task_id = {}
    board_data_by_context = {
        'word_salad': {},
        'raddle': {},
    }
    word_salad_offer_share = _word_salad_offer_share_hash(game, task_group)
    for t in tasks:
        adapter = get_daily_board_adapter(
            t.task_type,
            game_id=getattr(game, 'id', None),
        )
        if adapter is None:
            continue
        ai = attempts_info_by_task_id.get(t.id)
        share_title = None
        if (
            adapter.context_key == 'raddle'
            and str(getattr(game, 'id', '')) != LADDER_GAME_ID
            and placement is not None
        ):
            share_title = raddle_share_title(game, placement.number, t.number)
        data = adapter.prepare(
            game=game,
            task=t,
            placement=placement,
            attempts_info=ai,
            team=team,
            user=user,
            anon_key=anon_key,
            mode=mode,
            replay_slot=replay_slot,
            resolve_chain_state=chain_state_for_actor,
            raddle_ui_state_for_actor=raddle_ui_state_for_actor,
            share_title=share_title,
        )
        if data is None:
            continue
        if t.task_type == 'word_salad' and word_salad_offer_share:
            # Share hash is part of the write authorization contract for custom Salad.
            data['offer_share'] = word_salad_offer_share
        board_data_by_context.setdefault(adapter.context_key, {})[t.id] = data
        daily_board_context_key_by_task_id[t.id] = adapter.context_key
        daily_board_template_by_task_id[t.id] = adapter.body_template
        daily_board_wrapper_by_task_id[t.id] = adapter.body_wrapper
        daily_board_adapter_by_task_id[t.id] = adapter
    return {
        'board_data_by_context': board_data_by_context,
        'daily_board_context_key_by_task_id': daily_board_context_key_by_task_id,
        'daily_board_template_by_task_id': daily_board_template_by_task_id,
        'daily_board_wrapper_by_task_id': daily_board_wrapper_by_task_id,
        'daily_board_adapter_by_task_id': daily_board_adapter_by_task_id,
    }
