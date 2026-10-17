"""HTTP API for daily-game active solving time."""

from __future__ import annotations

import json
from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods
from django.db import OperationalError
from games.middleware.db_transient_response import mysql_error_code

from games.daily_section import (
    is_daily_team_timing_game,
    is_scheduled_game,
    scheduled_number_is_public,
)
from games.daily_timing import (
    ACTION_RESUME,
    ACTION_START,
    MUTATING_ACTIONS,
    TimingEventConflict,
    apply_timing_event,
    empty_snapshot,
    lookup_timing,
    snapshot,
)
from games.db_retry import is_mysql_retryable_lock_error
from games.models import Attempt, Game, GameTaskGroup
from games.analytics_identity import gameplay_anon_key
from games.gameplay_context import context_error_response, validate_gameplay_context
from games.views.util import effective_play_mode, has_profile, has_team


def _json_error(code, http_status=400):
    body = empty_snapshot()
    body['ok'] = False
    body['error'] = code
    return JsonResponse(body, status=http_status)


def _client_event_time(payload):
    """Return a bounded aware client timestamp used only for replay ordering."""
    raw = payload.get('client_occurred_at') or payload.get('occurred_at')
    if not isinstance(raw, str):
        return None
    value = parse_datetime(raw.strip())
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.utc)
    now = timezone.now()
    if value < now - timedelta(days=1) or value > now + timedelta(days=1):
        return None
    return value


def _timing_int(payload, key, *, default=None):
    raw = payload.get(key)
    if raw in (None, ''):
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'bad_{}'.format(key)
    if value < 0:
        return None, 'bad_{}'.format(key)
    return value, None


def _payload(request):
    if request.method != 'POST':
        return {}
    content_type = (request.content_type or '').lower()
    if 'json' in content_type:
        try:
            data = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, TypeError, UnicodeDecodeError):
            return {'_payload_error': 'invalid_json'}
        return data if isinstance(data, dict) else {}
    data = request.POST.dict() if hasattr(request.POST, 'dict') else dict(request.POST)
    return data


def _resolve_actor(request, game):
    if request.user.is_authenticated:
        if not has_profile(request.user):
            # Censorly permits authenticated players without a Profile; keep
            # their official solve timing on the User actor just like its
            # guess endpoint does.
            if game.id == 'censorly':
                return None, request.user, None
            return None, None, None
        from games.views.new_ui import _get_play_mode
        play_mode, _ = _get_play_mode(request, game.project_id)
        play_mode = effective_play_mode(play_mode, game, user=request.user)
        if play_mode == 'team' and is_daily_team_timing_game(game.id):
            if not has_team(request.user):
                return None, None, None
            return request.user.profile.team_on, None, None
        return None, request.user, None
    anon_key = gameplay_anon_key(request)
    if anon_key:
        return None, None, str(anon_key)
    return None, None, None


def _load_daily_target(request, game_id, number, project_id=None):
    game_qs = Game.objects.filter(id=game_id)
    if project_id:
        game_qs = game_qs.filter(project_id=project_id)
    game = get_object_or_404(game_qs)
    raw_number = str(number or '').strip()
    random_link = _random_game_timing_link(game, raw_number)
    is_numbered = bool(
        random_link is None
        and raw_number
        and raw_number.replace('.', '', 1).isdigit()
    )
    if is_numbered:
        if is_scheduled_game(game.id) and not scheduled_number_is_public(game, raw_number) and not request.user.is_staff:
            return None, None, None, None, None, _json_error('not_published', 404)
        from games.club_access import user_can_access_scheduled_number

        if not user_can_access_scheduled_number(request.user, game, raw_number):
            return None, None, None, None, None, _json_error('club_required', 403)
        link = GameTaskGroup.objects.filter(
            game=game, number=raw_number, is_deferred=False,
        ).select_related('task_group').first()
    else:
        link = random_link
    if link is None:
        return None, None, None, None, None, _json_error('missing', 404)
    if not is_numbered and game.id == 'censorly':
        from games.views.censorly_views import _preview_denied

        denied = _preview_denied(request, game)
        if denied is not None:
            return None, None, None, None, None, denied
    team, user, anon_key = _resolve_actor(request, game)
    if team is None and user is None and anon_key is None:
        return None, None, None, None, None, _json_error('no_actor', 400)
    if request.user.is_authenticated and user is None and team is None:
        return None, None, None, None, None, _json_error('no_profile_or_team', 403)
    if is_numbered:
        if team is not None and not game.has_access('play', team=team):
            return None, None, None, None, None, _json_error('team_access_required', 403)
        if team is None and not game.has_access(
            'read_googledoc', team=None, attempt=Attempt(time=timezone.now()),
        ):
            return None, None, None, None, None, _json_error('play_access_required', 403)
        if team is None and not game.is_playable:
            return None, None, None, None, None, _json_error('not_playable', 404)
    return game, link.task_group, team, user, anon_key, None


def _random_game_timing_link(game, share_hash):
    """Resolve only permanent random games; proposals and daily shares stay out."""
    from games.alphabetty_daily import ALPHABETTY_GAME_ID
    from games.censorly import CENSORLY_GAME_ID
    from games.models import RandomAlphabettyGame, RandomCensorlyGame
    from games.placement_share import placement_by_share_hash

    if game.id not in (ALPHABETTY_GAME_ID, CENSORLY_GAME_ID):
        return None
    share_hash = str(share_hash or '').strip().lower()
    link = placement_by_share_hash(game, share_hash)
    if link is None or link.is_deferred:
        return None
    if game.id == ALPHABETTY_GAME_ID:
        is_random = RandomAlphabettyGame.objects.filter(
            task_group_id=link.task_group_id,
            share_hash=share_hash,
        ).exists()
    else:
        is_random = RandomCensorlyGame.objects.filter(
            task_group_id=link.task_group_id,
            share_hash=share_hash,
        ).exists()
    return link if is_random else None


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
    timing_url=None,
):
    enabled = bool(
        game is not None
        and placement is not None
        and not is_offer
        and (play_mode != 'team' or team is not None)
        and replay_slot is None
        and not official_completed
    )
    state = empty_snapshot()
    url = ''
    gameplay_context_token = ''
    if enabled:
        from games.tasks.navigation import play_url_for_task_group
        url = timing_url or '{}timing/'.format(play_url_for_task_group(game, placement.number))
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
        'daily_timing_team_mode': bool(team is not None),
    }


@require_http_methods(['GET', 'POST'])
def daily_solve_timing(
    request, game_id, number=None, task_group_number=None, project_id=None,
    share_hash=None,
):
    response = _daily_solve_timing_impl(
        request, game_id, number=number, task_group_number=task_group_number,
        project_id=project_id, share_hash=share_hash,
    )
    # A transient failure in response middleware (for example DB-backed
    # session persistence) happens after the view completed. Clear retry
    # eligibility before Django starts that response phase.
    request._interoves_timing_pre_mutation = False
    request._interoves_timing_read_only = False
    return response


def _daily_solve_timing_impl(
    request, game_id, number=None, task_group_number=None, project_id=None,
    share_hash=None,
):
    # The early target/session reads precede timing writes. Mark that stage so
    # connection loss there can be retried safely even by older clients.
    request._interoves_timing_pre_mutation = request.method == 'POST'
    # GET for this specific timing endpoint only performs target resolution
    # and snapshot reads. The middleware's exception signal snapshots this
    # marker if a DB error occurs before the view returns.
    request._interoves_timing_read_only = request.method == 'GET'
    number = number if number is not None else task_group_number
    number = number if number is not None else share_hash
    if project_id is None:
        root_game = get_object_or_404(Game, id=game_id)
        if root_game.project_id not in ('main', 'sections'):
            path = '/{}/games/{}/{}/timing/'.format(root_game.project_id, root_game.id, number)
            query = request.META.get('QUERY_STRING')
            if query:
                path = '{}?{}'.format(path, query)
            from django.shortcuts import redirect
            return redirect(path, permanent=True)
    game, task_group, team, user, anon_key, err = _load_daily_target(
        request, game_id, number, project_id=project_id,
    )
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
        # Advertise safe stable-event handling to clients before they opt into
        # retrying a request whose commit outcome may be unknown.
        body['timing_event_capability'] = 'stable-event-v1'
        return JsonResponse(body)

    payload = _payload(request)
    if payload.get('_payload_error'):
        return _json_error(payload['_payload_error'], 400)
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
    raw_action = payload.get('action') or ACTION_START
    if not isinstance(raw_action, str):
        return _json_error('bad_action', 400)
    action = raw_action.strip()
    if action not in MUTATING_ACTIONS:
        return _json_error('bad_action', 400)
    request.interoves_timing_game_id = game.id
    request.interoves_timing_task_group_id = task_group.id
    request.interoves_timing_action = action
    session_id = payload.get('session_id')
    event_id = payload.get('event_id')
    if not isinstance(session_id, str) or not session_id.strip():
        return _json_error('bad_session_id', 400)
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id.strip()) > 64:
        return _json_error('bad_event_id', 400)
    # Only clients on the stable-event protocol may be told to retry an
    # unknown commit outcome. Legacy clients generated a fresh event ID on
    # each start retry, so treating their retry as idempotent is unsafe.
    request._interoves_timing_event_idempotent = (
        payload.get('event_protocol') == 1
        and payload.get('event_capability') == 'stable-event-v1'
    )
    seq, seq_error = _timing_int(payload, 'seq', default=0)
    if seq_error:
        return _json_error(seq_error, 400)
    claimed_ms, claimed_error = _timing_int(payload, 'claimed_ms')
    if claimed_error:
        return _json_error(claimed_error, 400)
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
    request._interoves_timing_pre_mutation = False
    try:
        result = apply_timing_event(
            game=game,
            task_group=task_group,
            team=team,
            user=user,
            anon_key=anon_key,
            action=action,
            session_id=session_id,
            event_id=event_id,
            seq=seq,
            claimed_ms=claimed_ms,
            client_occurred_at=_client_event_time(payload),
            create=True,
            replay_slot=replay_slot,
            timing_request=request,
        )
    except TimingEventConflict:
        return _json_error('event_id_conflict', 409)
    except OperationalError as exc:
        # A competing timing mutation can exhaust the transaction retries.
        # This is transient contention, not a broken request: let the timer
        # retry instead of turning it into a Telegram-worthy HTTP 500.
        mysql_code = mysql_error_code(exc)
        if is_mysql_retryable_lock_error(exc) or (
            mysql_code in (2006, 2013)
            and getattr(request, '_interoves_timing_event_idempotent', False)
        ):
            request._interoves_mysql_error_code = mysql_code
            response = _json_error('temporarily_unavailable', 503)
            response['Retry-After'] = '2'
            response['Cache-Control'] = 'no-store'
            return response
        raise
    if not result.get('exists') and action not in (ACTION_START, ACTION_RESUME):
        result['ok'] = False
        result['error'] = 'missing'
        return JsonResponse(result, status=404)
    result['ok'] = True
    return JsonResponse(result)
