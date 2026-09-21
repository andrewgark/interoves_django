"""HTTP API for daily-game active solving time."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from games.daily_section import is_daily_team_timing_game, is_daily_timing_game, scheduled_number_is_public
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
from games.views.new_ui import NEW_UI_SECTIONS_PROJECT
from games.analytics_identity import gameplay_anon_key
from games.gameplay_context import context_error_response, validate_gameplay_context
from games.views.util import effective_play_mode, has_profile, has_team


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


def _resolve_actor(request, game):
    if request.user.is_authenticated:
        if not has_profile(request.user):
            return None, None, None
        from games.views.new_ui import _get_play_mode
        play_mode, _ = _get_play_mode(request, game.project_id)
        play_mode = effective_play_mode(play_mode, game, user=request.user)
        if play_mode == 'team':
            if not has_team(request.user) or not is_daily_team_timing_game(game.id):
                return None, None, None
            return request.user.profile.team_on, None, None
        return None, request.user, None
    anon_key = gameplay_anon_key(request)
    if anon_key:
        return None, None, str(anon_key)
    return None, None, None


def _load_daily_target(request, game_id, number):
    if not is_daily_timing_game(game_id):
        return None, None, None, None, None, _json_error('not_daily', 404)
    game = get_object_or_404(Game, id=game_id, project_id=NEW_UI_SECTIONS_PROJECT)
    raw_number = str(number or '').strip()
    if not raw_number or not raw_number.replace('.', '', 1).isdigit():
        return None, None, None, None, None, _json_error('not_daily', 404)
    if not scheduled_number_is_public(game, raw_number) and not request.user.is_staff:
        return None, None, None, None, None, _json_error('not_published', 404)
    from games.club_access import user_can_access_scheduled_number

    if not user_can_access_scheduled_number(request.user, game, raw_number):
        return None, None, None, None, None, _json_error('club_required', 403)
    link = GameTaskGroup.objects.filter(game=game, number=raw_number).select_related('task_group').first()
    if link is None:
        return None, None, None, None, None, _json_error('missing', 404)
    team, user, anon_key = _resolve_actor(request, game)
    if team is None and user is None and anon_key is None:
        return None, None, None, None, None, _json_error('no_actor', 400)
    if request.user.is_authenticated and user is None and team is None:
        return None, None, None, None, None, _json_error('no_profile_or_team', 403)
    if team is not None and not game.has_access('play', team=team):
        return None, None, None, None, None, _json_error('team_access_required', 403)
    return game, link.task_group, team, user, anon_key, None


def daily_timing_page_context(
    request,
    game,
    placement,
    *,
    team=None,
    user=None,
    anon_key=None,
    play_mode='personal',
    is_offer=False,
    replay_slot=None,
    official_completed=False,
):
    enabled = bool(
        game is not None
        and placement is not None
        and is_daily_timing_game(game.id)
        and not is_offer
        and (play_mode != 'team' or (team is not None and is_daily_team_timing_game(game.id)))
        and replay_slot is None
        and not official_completed
    )
    state = empty_snapshot()
    url = ''
    gameplay_context_token = ''
    if enabled:
        url = '/{}/{}/timing/'.format(game.id, placement.number)
        from games.gameplay_context import issue_gameplay_context
        gameplay_context_token = issue_gameplay_context(
            task_group=placement.task_group,
            game=game,
            team=team,
            user=user,
            anon_key=anon_key,
            replay_slot=replay_slot,
        )
        if team is not None or user is not None or anon_key:
            state = snapshot(lookup_timing(
                game=game,
                task_group=placement.task_group,
                team=team,
                user=user,
                anon_key=anon_key,
                replay_slot=replay_slot,
            ))
    return {
        'daily_timing_enabled': enabled,
        'daily_timing': state,
        'daily_timing_url': url,
        'daily_timing_context_token': gameplay_context_token,
    }


@require_http_methods(['GET', 'POST'])
def daily_solve_timing(request, game_id, number=None, task_group_number=None):
    number = number if number is not None else task_group_number
    game, task_group, team, user, anon_key, err = _load_daily_target(request, game_id, number)
    if err is not None:
        return err

    if request.method == 'GET':
        session_id = request.GET.get('session_id') or ''
        from games.replay import active_replay
        replay_slot = active_replay(
            request=request, game=game, task_group=task_group,
            team=team, user=user, anon_key=anon_key,
        )
        body = snapshot(
            lookup_timing(
                game=game,
                task_group=task_group,
                team=team,
                user=user,
                anon_key=anon_key,
                replay_slot=replay_slot,
            ),
            session_id=session_id or None,
        )
        body['ok'] = True
        return JsonResponse(body)

    payload = _payload(request)
    from games.replay import replay_for_request, StaleReplayError, _official_exists
    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task_group,
            team=team, user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return _json_error('stale_replay', 409)
    if replay_slot is None and _official_exists(
        game=game, task_group=task_group, team=team, user=user, anon_key=anon_key,
    ):
        return _json_error('replay_required', 409)
    action = (payload.get('action') or ACTION_START).strip()
    if action not in MUTATING_ACTIONS:
        return _json_error('bad_action', 400)
    context_error = validate_gameplay_context(
        request,
        task_group=task_group,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
    )
    if context_error:
        response = context_error_response(context_error)
        if response is not None:
            return response
    result = apply_timing_event(
        game=game,
        task_group=task_group,
        team=team,
        user=user,
        anon_key=anon_key,
        action=action,
        session_id=payload.get('session_id'),
        event_id=payload.get('event_id') or '',
        seq=payload.get('seq') or 0,
        claimed_ms=payload.get('claimed_ms'),
        create=True,
        replay_slot=replay_slot,
    )
    if not result.get('exists') and action not in (ACTION_START, ACTION_RESUME):
        result['ok'] = False
        result['error'] = 'missing'
        return JsonResponse(result, status=404)
    result['ok'] = True
    return JsonResponse(result)
