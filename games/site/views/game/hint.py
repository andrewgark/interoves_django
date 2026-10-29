import dataclasses

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from games.gameplay.effects import apply_move_effects
from games.gameplay.hints import create_hint_attempt  # also re-exported to older importers
from games.gameplay.moves import REPLAY_REQUIRED, MoveContext, official_game_completed
from games.models import Hint
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
from games.site.session.gameplay_context import context_error_response
from games.site.views.game.context import resolve_move_context, submitter_profile
from games.site.views.game.render import update_task_html


def process_send_hint_attempt(request, task_id):
    task = get_public_task_or_404(task_id)
    if task.task_type == 'word_salad':
        from games.site.views.game.attempt import process_send_attempt
        return process_send_attempt(request, task_id)
    ctx = resolve_move_context(request, task)
    if not isinstance(ctx, MoveContext):
        return ctx
    team, user, anon_key, game, replay_slot = ctx.team, ctx.user, ctx.anon_key, ctx.game, ctx.replay_slot

    if task.task_type == 'autohint':
        from games.puzzles.markers.autohint import AutohintPuzzle
        raise InvalidFormException(AutohintPuzzle.manual_hint_message())

    if replay_slot is None and official_game_completed(ctx):
        return dict(REPLAY_REQUIRED)

    hint_number = str(request.POST.get('hint_number', '')).strip()
    if not hint_number:
        raise InvalidFormException('hint_number is required')
    hint = get_object_or_404(Hint, task=task, number=hint_number)

    hint_attempt, current_mode = create_hint_attempt(
        hint, team=team, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot,
        requested_by_profile=submitter_profile(request),
    )

    result = {
        'status': 'ok',
        'task_id': task.id,
    }
    # A hint never solves the task; a real request (before solving) starts the game.
    effects = apply_move_effects(
        dataclasses.replace(ctx, mode=current_mode),
        started=hint_attempt.is_real_request,
        may_complete=False,
        source='hint',
        observer=request,
    )
    if effects.analytics_events:
        result['analytics_events'] = effects.analytics_events
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
