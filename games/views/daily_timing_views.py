"""HTTP API for daily-game active solving time."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from games.daily_section import is_daily_timing_game, scheduled_number_is_public
from games.daily_timing import (
    ACTION_RESUME,
    ACTION_START,
    MUTATING_ACTIONS,
    apply_timing_event,
    empty_snapshot,
    lookup_timing,
    snapshot,
)
from games.models import Game, GameTaskGroup
from games.views.new_ui import NEW_UI_SECTIONS_PROJECT, _anon_key_from_request
from games.views.util import has_profile


def _json_error(code, http_status=400):
    body = empty_snapshot()
    body['ok'] = False
    body['error'] = code
    return JsonResponse(body, status=http_status)


def _payload(request):
    if request.method != 'POST':
        return {}
    content_type = (request.content_type or '').lower()
    if 'json' in content_type:
        try:
            data = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, TypeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    data = request.POST.dict() if hasattr(request.POST, 'dict') else dict(request.POST)
    return data


def _resolve_actor(request, payload):
    if request.user.is_authenticated:
        return request.user, None
    anon_key = (
        payload.get('anon_key')
        or _anon_key_from_request(request)
        or request.headers.get('X-Interoves-Anon')
    )
    if anon_key:
        return None, str(anon_key)
    return None, None


def _load_daily_target(request, game_id, number):
    if not is_daily_timing_game(game_id):
        return None, None, None, None, _json_error('not_daily', 404)
    game = get_object_or_404(Game, id=game_id, project_id=NEW_UI_SECTIONS_PROJECT)
    raw_number = str(number or '').strip()
    if not raw_number or not raw_number.replace('.', '', 1).isdigit():
        return None, None, None, None, _json_error('not_daily', 404)
    if not scheduled_number_is_public(game, raw_number) and not request.user.is_staff:
        return None, None, None, None, _json_error('not_published', 404)
    from games.club_access import user_can_access_scheduled_number

    if not user_can_access_scheduled_number(request.user, game, raw_number):
        return None, None, None, None, _json_error('club_required', 403)
    link = GameTaskGroup.objects.filter(game=game, number=raw_number).select_related('task_group').first()
    if link is None:
        return None, None, None, None, _json_error('missing', 404)
    payload = _payload(request)
    user, anon_key = _resolve_actor(request, payload)
    if user is None and not anon_key:
        return None, None, None, None, _json_error('no_anon', 400)
    if user is not None and not has_profile(user) and request.user.is_authenticated:
        return None, None, None, None, _json_error('no_profile', 403)
    return game, link.task_group, user, anon_key, None


def daily_timing_page_context(
    request,
    game,
    placement,
    *,
    user=None,
    anon_key=None,
    play_mode='personal',
    is_offer=False,
):
    enabled = bool(
        game is not None
        and placement is not None
        and is_daily_timing_game(game.id)
        and not is_offer
        and play_mode != 'team'
    )
    state = empty_snapshot()
    url = ''
    if enabled:
        url = '/{}/{}/timing/'.format(game.id, placement.number)
        if user is not None or anon_key:
            state = snapshot(lookup_timing(
                game=game,
                task_group=placement.task_group,
                user=user,
                anon_key=anon_key,
            ))
    return {
        'daily_timing_enabled': enabled,
        'daily_timing': state,
        'daily_timing_url': url,
    }


@require_http_methods(['GET', 'POST'])
def daily_solve_timing(request, game_id, number=None, task_group_number=None):
    number = number if number is not None else task_group_number
    game, task_group, user, anon_key, err = _load_daily_target(request, game_id, number)
    if err is not None:
        return err

    if request.method == 'GET':
        session_id = request.GET.get('session_id') or ''
        body = snapshot(
            lookup_timing(
                game=game,
                task_group=task_group,
                user=user,
                anon_key=anon_key,
            ),
            session_id=session_id or None,
        )
        body['ok'] = True
        return JsonResponse(body)

    payload = _payload(request)
    action = (payload.get('action') or ACTION_START).strip()
    if action not in MUTATING_ACTIONS:
        return _json_error('bad_action', 400)
    result = apply_timing_event(
        game=game,
        task_group=task_group,
        user=user,
        anon_key=anon_key,
        action=action,
        session_id=payload.get('session_id'),
        event_id=payload.get('event_id') or '',
        seq=payload.get('seq') or 0,
        claimed_ms=payload.get('claimed_ms'),
        create=True,
    )
    if not result.get('exists') and action not in (ACTION_START, ACTION_RESUME):
        result['ok'] = False
        result['error'] = 'missing'
        return JsonResponse(result, status=404)
    result['ok'] = True
    return JsonResponse(result)
