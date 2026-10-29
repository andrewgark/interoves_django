from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from games.gameplay.hints import create_hint_attempt  # also re-exported to older importers
from games.identity import ActorRefusal, has_profile, resolve_gameplay_actor
from games.models import Hint
from games.ops.analytics.events import register_started_game
from games.realtime.track_compat import track_actor_task_change
from games.site.http.exceptions import (
    DuplicateAttemptException,
    InvalidFormException,
    NoGameAccessException,
    NotAllRequiredHintsTakenException,
)
from games.site.http.request_util import (
    get_public_task_or_404,
    is_browser_form_submission,
    redirect_after_browser_submission,
)
from games.site.session.gameplay_context import context_error_response, validate_gameplay_context
from games.site.session.play_mode import effective_play_mode
from games.site.session.replay import StaleReplayError, replay_for_request
from games.site.views.game.context import (
    game_from_request_for_task,
    unpublished_scheduled_task_response,
)
from games.site.views.game.render import update_task_html


def _get_play_mode(request, game):
    # Navigation stores the mode in the session, which is shared by tabs.  A
    # gameplay POST must keep the mode of the page that initiated it.
    mode = request.headers.get('X-Interoves-Play-Mode')
    if mode not in ('team', 'personal'):
        mode = request.session.get('play_mode_{}'.format(game.project_id or 'main'))
    if mode not in ('team', 'personal'):
        mode = 'personal' if game.project_id == 'sections' else 'team'
    return effective_play_mode(mode, game, user=request.user)


def process_send_hint_attempt(request, task_id):
    task = get_public_task_or_404(task_id)
    if task.task_type == 'word_salad':
        from games.site.views.game.attempt import process_send_attempt
        return process_send_attempt(request, task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        return {'status': 'ambiguous_game'}
    unpublished = unpublished_scheduled_task_response(request, game, task)
    if unpublished is not None:
        return unpublished
    from games.club.access.archive import user_can_access_task_archive
    if not user_can_access_task_archive(
        request.user, game, task, offer_share=request.POST.get('offer_share'),
    ):
        raise NoGameAccessException('Club subscription required for archived game')
    play_mode = _get_play_mode(request, game)
    resolved = resolve_gameplay_actor(request, game, play_mode)
    if isinstance(resolved, ActorRefusal):
        return {'status': resolved.status}
    team = resolved.team
    user = resolved.user
    anon_key = resolved.anon_key

    if task.task_type == 'autohint':
        from games.puzzles.markers.autohint import AutohintPuzzle
        raise InvalidFormException(AutohintPuzzle.manual_hint_message())

    context_error = validate_gameplay_context(
        request, task=task, game=game, team=team, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error

    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task.task_group,
            team=team, user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return {'status': 'error', 'error': 'stale_replay', 'reload_required': True}
    if replay_slot is None:
        from games.site.session.replay import _official_exists
        if _official_exists(game=game, task_group=task.task_group, team=team, user=user, anon_key=anon_key):
            return {'status': 'error', 'error': 'replay_required', 'reload_required': True}

    hint_number = str(request.POST.get('hint_number', '')).strip()
    if not hint_number:
        raise InvalidFormException('hint_number is required')
    hint = get_object_or_404(Hint, task=task, number=hint_number)

    hint_attempt, current_mode = create_hint_attempt(
        hint, team=team, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot,
        requested_by_profile=(
            request.user.profile
            if request.user.is_authenticated and has_profile(request.user) else None
        ),
    )

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    if hint_attempt.is_real_request:
        analytics_events = [] if replay_slot is not None else register_started_game(
            team=team,
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        )
        if analytics_events:
            result['analytics_events'] = analytics_events
    update_html = update_task_html(
        request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        replay_slot=replay_slot,
    )
    if replay_slot is None:
        track_actor_task_change(
            task,
            team=team,
            current_mode=current_mode,
            update_html=update_html,
            request=request,
            game=game,
            user=user,
            anon_key=anon_key,
            reason='hint.taken',
        )
    result.update(update_html)
    return result


@require_POST
def send_hint_attempt(request, task_id):
    try:
        response = process_send_hint_attempt(request, task_id)
    except DuplicateAttemptException:
        response = {'status': 'duplicate'}
    except NotAllRequiredHintsTakenException:
        response = {'status': 'not_all_required_hints_taken'}
    except InvalidFormException:
        response = {'status': 'invalid_form'}
    except NoGameAccessException:
        response = {'status': 'no_access'}
    context_response = context_error_response(response)
    if context_response is not None:
        if is_browser_form_submission(request):
            return redirect_after_browser_submission(request)
        return context_response
    if is_browser_form_submission(request):
        return redirect_after_browser_submission(request)
    return JsonResponse(response) 
