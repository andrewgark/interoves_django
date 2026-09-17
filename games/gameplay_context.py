"""Signed, server-verifiable context for browser gameplay mutations."""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings
from django.core import signing
from django.http import JsonResponse


GAMEPLAY_CONTEXT_FIELD = 'gameplay_context'
GAMEPLAY_CONTEXT_VERSION = 1
GAMEPLAY_CONTEXT_SALT = 'interoves.gameplay-context.v1'
GAMEPLAY_CONTEXT_MAX_AGE = 60 * 60 * 24 * 14


def _anonymous_identifier(anon_key):
    """Use a stable non-reversible identifier inside the signed token."""
    if not anon_key:
        return None
    key = str(getattr(settings, 'SECRET_KEY', '')).encode('utf-8')
    return hmac.new(key, str(anon_key).encode('utf-8'), hashlib.sha256).hexdigest()


def anonymous_actor_fingerprint(anon_key):
    """Return the non-reversible anonymous identifier used in safe telemetry."""
    return _anonymous_identifier(anon_key)


def actor_descriptor(*, team=None, user=None, anon_key=None):
    if team is not None:
        return 'team', str(team.pk)
    if user is not None:
        return 'user', str(user.pk)
    if anon_key:
        return 'anon', _anonymous_identifier(anon_key)
    return None, None


def issue_gameplay_context(
    *, task=None, task_group=None, game, team=None, user=None, anon_key=None,
    replay_slot=None,
):
    actor_kind, actor_id = actor_descriptor(team=team, user=user, anon_key=anon_key)
    if not actor_kind:
        return ''
    payload = {
        'v': GAMEPLAY_CONTEXT_VERSION,
        'actor_kind': actor_kind,
        'actor_id': actor_id,
        'game_id': str(game.pk),
    }
    if task is not None:
        payload['task_id'] = str(task.pk)
    elif task_group is not None:
        payload['task_group_id'] = str(task_group.pk)
    else:
        raise ValueError('task or task_group is required')
    if replay_slot is not None:
        payload['replay_slot_id'] = str(replay_slot.pk)
        payload['replay_run_id'] = str(replay_slot.run_id)
    return signing.dumps(payload, salt=GAMEPLAY_CONTEXT_SALT, compress=True)


def _error(request, code, *, expected=None, expected_id=None, current=None):
    request.interoves_gameplay_context_result = code
    request.interoves_gameplay_context_expected = expected
    request.interoves_gameplay_context_current = current
    try:
        from games.auth_observability import log_auth_event, request_session_fingerprint
        current_user = getattr(request, 'user', None)
        log_auth_event(
            'gameplay_actor_context_mismatch'
            if 'mismatch' in code else 'gameplay_context_rejected',
            request,
            current_actor_kind=current,
            expected_actor_kind=expected,
            expected_actor_id=(str(expected_id)[:64] if expected == 'user' else expected_id),
            current_user_id=(
                str(current_user.pk)[:64]
                if getattr(current_user, 'is_authenticated', False) else None
            ),
            session_fingerprint=request_session_fingerprint(request),
            task_id=getattr(request, 'interoves_gameplay_task_id', None),
            error=code,
        )
    except Exception:
        pass
    return {
        'status': 'error',
        'error': code,
        'reload_required': True,
    }


def validate_gameplay_context(
    request, *, task=None, task_group=None, game, team=None, user=None, anon_key=None,
):
    """Validate page context before an actor-scoped mutation.

    Missing tokens are accepted during Phase A. A token, once present, is never
    trusted for ownership: current actor is independently resolved by the view.
    """
    token = (request.POST.get(GAMEPLAY_CONTEXT_FIELD) or request.headers.get(
        'X-Interoves-Gameplay-Context', ''
    )).strip()
    current_kind, current_id = actor_descriptor(
        team=team, user=user, anon_key=anon_key,
    )
    request.interoves_gameplay_request = True
    request.interoves_gameplay_actor_kind = current_kind
    request.interoves_gameplay_task_id = str(task.pk) if task is not None else None
    request.interoves_gameplay_task_group_id = str(task_group.pk) if task_group is not None else None
    request.interoves_replay_slot_id = None
    request.interoves_replay_run_id = None
    if not token:
        request.interoves_gameplay_context_result = 'missing_legacy'
        request.interoves_gameplay_context_expected = None
        request.interoves_gameplay_context_current = current_kind
        if getattr(settings, 'GAMEPLAY_CONTEXT_REQUIRE_TOKEN', False):
            return _error(request, 'gameplay_context_required', current=current_kind)
        return None

    try:
        payload = signing.loads(
            token,
            salt=GAMEPLAY_CONTEXT_SALT,
            max_age=GAMEPLAY_CONTEXT_MAX_AGE,
        )
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError):
        return _error(request, 'invalid_gameplay_context', current=current_kind)

    if not isinstance(payload, dict):
        return _error(request, 'invalid_gameplay_context', current=current_kind)
    request.interoves_replay_slot_id = payload.get('replay_slot_id')
    request.interoves_replay_run_id = payload.get('replay_run_id')
    expected_kind = payload.get('actor_kind')
    expected_id = payload.get('actor_id')
    if payload.get('v') != GAMEPLAY_CONTEXT_VERSION:
        return _error(request, 'invalid_gameplay_context', current=current_kind)
    task_matches = (
        task is not None and str(payload.get('task_id')) == str(task.pk)
    )
    task_group_matches = (
        task_group is not None
        and str(payload.get('task_group_id')) == str(task_group.pk)
    )
    if not (task_matches or task_group_matches) or str(payload.get('game_id')) != str(game.pk):
        return _error(
            request,
            'gameplay_context_task_mismatch',
            expected=expected_kind,
            expected_id=expected_id,
            current=current_kind,
        )
    if expected_kind != current_kind:
        return _error(
            request,
            'gameplay_actor_context_mismatch',
            expected=expected_kind,
            expected_id=expected_id,
            current=current_kind,
        )
    if expected_kind == 'team':
        actual_id = str(team.pk) if team is not None else None
    elif expected_kind == 'user':
        actual_id = str(user.pk) if user is not None else None
    else:
        actual_id = _anonymous_identifier(anon_key)
    if not hmac.compare_digest(str(expected_id or ''), str(actual_id or '')):
        return _error(
            request,
            'gameplay_actor_context_mismatch',
            expected=expected_kind,
            expected_id=expected_id,
            current=current_kind,
        )
    request.interoves_gameplay_context_result = 'valid'
    request.interoves_gameplay_context_expected = expected_kind
    request.interoves_gameplay_context_current = current_kind
    return None


def context_error_response(data):
    if not isinstance(data, dict):
        return None
    if data.get('reload_required'):
        return JsonResponse(data, status=409)
    return None
