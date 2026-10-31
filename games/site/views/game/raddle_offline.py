"""Лесенка без сети: the pack a page needs to keep playing offline.

POST /raddle_offline_pack/<task_id>/ returns the card pieces of every state
the actor can reach (games/puzzles/raddle/offline_pack.py), the card as it
is now, the word rules and the answer check. The page fetches it in the
background and assembles the card itself while the network is gone.
"""

import json

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.gameplay.attempts import client_spec_for_task
from games.gameplay.moves import MoveContext
from games.gameplay.offline_telemetry import record_client_events
from games.gameplay.policy import resolve_capabilities
from games.models import Attempt, GameTaskGroup
from games.puzzles.raddle.engine import resolve_assist_tiers
from games.puzzles.raddle.offline_pack import build_pack
from games.puzzles.raddle.puzzle import offline_run, raddle_board_source
from games.puzzles.raddle.transition import rules_config
from games.site.http.exceptions import NoGameAccessException
from games.site.http.request_util import get_public_task_or_404
from games.site.session.gameplay_context import context_error_response
from games.site.views.game.context import resolve_move_context
from games.site.views.game.render import TASK_CARD_TEMPLATE, new_ui_task_card_context
from games.task_group.daily_board_task_data import board_share_title


def ladder_offline_pack(request, ctx, mode, *, offline_timing=False):
    """The offline pack of ``ctx``'s ladder for its actor."""
    task, game = ctx.task, ctx.game
    placement = GameTaskGroup.objects.filter(game=game, task_group=task.task_group).first()
    if placement is not None:
        placement.task_group = task.task_group
    card = new_ui_task_card_context(
        request, task, ctx.team, mode, user=ctx.user, anon_key=ctx.anon_key, game=game,
        replay_slot=ctx.replay_slot, placement=placement,
    )
    # The card has loaded this ladder for the actor already.
    source = ((card or {}).get('raddle_data') or {}).get(task.id, {}).get('board_source')
    if source is None:
        source = raddle_board_source(
            game=game, task=task, placement=placement, attempts_info=None,
            team=ctx.team, user=ctx.user, anon_key=ctx.anon_key, mode=mode,
            replay_slot=ctx.replay_slot, share_title=board_share_title(game, placement, task),
        )
    if source is None:
        return None

    def make_ui(state):
        ui = source.build(state)['ui']
        # The solving time is only known once the server sees the last word.
        ui['elapsed_label'] = ''
        return ui

    pieces = build_pack(
        source.parsed,
        source.state,
        tiers=resolve_assist_tiers(source.state, source.board.hint_attempts),
        make_ui=make_ui,
        render=lambda template, extra: render_to_string(template, {**card, **extra}, request),
    )
    return {
        **pieces,
        'task_id': task.id,
        'revision': str(task.attempt_revision),
        'run': offline_run(ctx.replay_slot),
        'state': {key: source.state.get(key) or [] for key in ('solved_indices', 'used_hints')},
        'rules': rules_config(source.parsed),
        'spec': client_spec_for_task(task),
        'card_html': render_to_string(TASK_CARD_TEMPLATE, card, request),
        # The page lets the daily timer claim the offline stretch.
        'offline_timing': offline_timing,
    }


def process_raddle_offline_pack(request, task_id):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {'status': 'invalid'}
    ctx = resolve_move_context(request, task)
    if not isinstance(ctx, MoveContext):
        return ctx
    mode = ctx.game.get_current_mode(Attempt(time=timezone.now()))
    capabilities = resolve_capabilities(
        game=ctx.game, task_type=task.task_type, mode=mode,
        team=ctx.team, user=ctx.user, anon_key=ctx.anon_key,
    )
    if mode == 'tournament' or not capabilities.offline_attempts:
        return {'status': 'disabled'}
    pack = ladder_offline_pack(request, ctx, mode, offline_timing=capabilities.offline_timing)
    if pack is None:
        return {'status': 'invalid'}
    return {'status': 'ok', 'pack': pack}


@require_POST
def raddle_offline_pack(request, task_id):
    try:
        response = process_raddle_offline_pack(request, task_id)
    except NoGameAccessException:
        response = {'status': 'no_access'}
    context_response = context_error_response(response)
    if context_response is not None:
        return context_response
    return JsonResponse(response)


@require_POST
def offline_telemetry(request):
    """What went wrong with offline play in the browser (games.gameplay.offline_telemetry)."""
    if len(request.body) > 16 * 1024:
        return JsonResponse({'ok': False, 'error': 'too_large'}, status=413)
    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'bad_json'}, status=400)
    events = payload.get('events') if isinstance(payload, dict) else None
    return JsonResponse({'ok': True, 'accepted': record_client_events(request, events)})
