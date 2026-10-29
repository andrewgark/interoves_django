import dataclasses
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.gameplay.attempts import check_attempt  # noqa: F401  (re-exported to older importers)
from games.gameplay.client_checks import CLIENT_VERDICTS
from games.gameplay.client_events import parse_client_event_id
from games.gameplay.effects import apply_move_effects
from games.gameplay.moves import REPLAY_REQUIRED, MoveContext, MoveInput, submit_move
from games.identity import ActorRefusal, resolve_gameplay_actor
from games.models import Attempt
from games.ops.analytics.events import (
    is_task_completion_state,
)
from games.puzzles import POSTED_TEXT_TASK_TYPES, get_puzzle, parse_posted_text
from games.puzzles.alphabetty import AlphabettyPuzzle
from games.puzzles.core.primitives import MoveRefusal, MoveResponse
from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.replacements.grid import GridPuzzle
from games.puzzles.salad import SaladSync
from games.realtime.track_compat import track_actor_task_change
from games.site.forms import AttemptForm
from games.site.http.exceptions import (
    ClientEventConflictException,
    DuplicateAttemptException,
    InvalidFormException,
    NoGameAccessException,
    TooManyAttemptsException,
)
from games.site.http.middleware.request_timing import timing_phase
from games.site.http.request_util import (
    get_public_task_or_404,
    is_browser_form_submission,
    redirect_after_browser_submission,
)
from games.site.session.gameplay_context import context_error_response
from games.site.views.game.context import (
    game_from_request_for_task,
    gameplay_play_mode,
    resolve_move_context,
    submitter_profile,
)
from games.site.views.game.render import update_task_html


def _raddle_stale_submit_response(request, outcome):
    """
    Устаревший UI (bfcache / смена anon↔login): форма на некрайнем или уже
    решённом слове. Attempt не записан — только синхронизируем HTML.
    """
    ctx = outcome.ctx
    result = outcome.stale
    update_html = update_task_html(
        request, ctx.task, ctx.team, ctx.mode, user=ctx.user, anon_key=ctx.anon_key, game=ctx.game,
        replay_slot=ctx.replay_slot,
    )
    if ctx.replay_slot is None:
        track_actor_task_change(
            ctx.task,
            team=ctx.team,
            update_html=update_html,
            request=request,
            game=ctx.game,
            user=ctx.user,
            anon_key=ctx.anon_key,
            current_mode=ctx.mode,
            reason='raddle.stale_ui_sync',
        )
    result.update(update_html)
    return result


def get_first_new_hint(task, team):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.key_sort())
    for hint in hints:
        if len(HintAttempt.objects.filter(team=team, hint=hint)) == 0:
            return hint
    return None


def process_send_attempt(request, task_id):
    task = get_public_task_or_404(task_id)

    ctx = resolve_move_context(request, task)
    if not isinstance(ctx, MoveContext):
        return ctx
    move = read_move(task, request.POST)
    if isinstance(move, MoveRefusal):
        return {'status': move.status}
    if isinstance(move, SaladSync):
        return _process_word_salad_sync_finds(request, ctx)

    outcome = submit_move(ctx, move, submitter_profile=submitter_profile(request), observer=request)
    if outcome.kind == 'replay_required':
        return dict(REPLAY_REQUIRED)
    if outcome.kind == 'stale_word':
        return _raddle_stale_submit_response(request, outcome)
    if outcome.kind == 'replayed':
        return _replayed_move_response(request, outcome, move)

    with timing_phase(request, 'response_payload'):
        result = {
            'status': 'ok',
            'task_id': task.id,
        }
    effects = outcome.effects
    if effects.daily_timing:
        result['daily_timing'] = effects.daily_timing
    if effects.replay_available:
        result['replay_available'] = True
    if effects.analytics_events:
        result['analytics_events'] = effects.analytics_events
    return _move_response(request, result, outcome, move)


def read_move(task, post):
    """Read the move of ``task`` from a POST-shaped mapping.

    Returns a ``MoveInput``, a ``MoveRefusal`` with the JSON status, or
    ``SaladSync`` for the Word Salad find sync. Raises
    ``InvalidFormException`` for an invalid ordinary answer.
    """
    client_event_id = parse_client_event_id(post)
    client_verdict = post.get('client_verdict')
    if client_verdict not in CLIENT_VERDICTS:
        client_verdict = None
    if task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint', 'proportions'):
        form = AttemptForm(post)
        if not form.is_valid():
            raise InvalidFormException('attempt form {} is not valid'.format(form))
        return MoveInput(
            text=form.save(commit=False).text,
            client_event_id=client_event_id,
            client_verdict=client_verdict,
        )
    if task.task_type in POSTED_TEXT_TASK_TYPES:
        move = parse_posted_text(task.task_type, post)
        if isinstance(move, MoveRefusal):
            return move
        return MoveInput(text=move.text, client_event_id=client_event_id, client_verdict=client_verdict)

    puzzle = get_puzzle(task.task_type)
    reader = getattr(puzzle, 'read_post', None) if puzzle is not None else None
    if reader is None and task.task_type == 'alphabetty':
        reader = AlphabettyPuzzle.read_post
    if reader is None and task.task_type == 'grid-puzzle':
        reader = GridPuzzle.read_post
    if reader is None:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    move = reader(post, task)
    status = getattr(move, 'status', None)
    if status is not None:
        return MoveRefusal(status)
    if isinstance(move, SaladSync):
        return move
    word_index = getattr(move, 'word_index', None)
    keeper = getattr(puzzle, 'persist_wrong', None) if puzzle is not None else None
    return MoveInput(
        text=move.text if hasattr(move, 'text') else move.word,
        word_index=word_index,
        word=move.word if word_index is not None else None,
        starts_game=bool(getattr(move, 'starts_game', False)),
        persist_wrong=keeper(post, getattr(move, 'action', None)) if keeper is not None else True,
        client_event_id=client_event_id,
        client_verdict=client_verdict,
    )


def _replayed_move_response(request, outcome, move):
    """Answer a resent move with the attempt its first delivery produced."""
    result = {
        'status': 'ok',
        'task_id': outcome.ctx.task.id,
        'client_event_replayed': True,
    }
    return _move_response(request, result, outcome, move)


def _move_response(request, result, outcome, move):
    """Add the verdict, puzzle fields and task HTML to a processed move."""
    ctx = outcome.ctx
    task, attempt = ctx.task, outcome.attempt
    # The transport status above only says that the submission was processed.
    # Give the new UI the persisted verdict as well, so ordinary tasks can show
    # explicit feedback without trying to infer it from freshly rendered HTML.
    # Raddle keeps its own contract and does not add attempt_status.
    puzzle = get_puzzle(task.task_type)
    if outcome.persisted and getattr(puzzle, 'report_attempt_status', True):
        result['attempt_status'] = attempt.status
        result['attempt_id'] = attempt.id
    response = MoveResponse(
        result=result,
        attempt=attempt,
        attempt_persisted=outcome.persisted,
        user=ctx.user,
        anon_key=ctx.anon_key,
        word_index=move.word_index,
        word=move.word,
    )
    writer = getattr(puzzle, 'write_response', None) if puzzle is not None else None
    if writer is None and task.task_type == 'grid-puzzle':
        writer = GridPuzzle.write_response
    if writer is not None:
        writer(response)

    include = getattr(puzzle, 'include_task_html', None) if puzzle is not None else None
    need_task_html = True if include is None else include(result)
    if need_task_html:
        with timing_phase(request, 'render_task'):
            update_html = update_task_html(
                request, task, ctx.team, ctx.mode, user=ctx.user, anon_key=ctx.anon_key, game=ctx.game,
                replay_slot=ctx.replay_slot,
            )
        if ctx.replay_slot is None:
            with timing_phase(request, 'schedule_realtime'):
                track_actor_task_change(
                    task,
                    team=ctx.team,
                    update_html=update_html,
                    request=request,
                    game=ctx.game,
                    user=ctx.user,
                    anon_key=ctx.anon_key,
                    current_mode=ctx.mode,
                    reason='attempt.submitted',
                )
        result.update(update_html)
    return result


def _process_word_salad_sync_finds(request, ctx):
    task, game, team, user, anon_key, replay_slot = (
        ctx.task, ctx.game, ctx.team, ctx.user, ctx.anon_key, ctx.replay_slot,
    )
    try:
        raw_words = json.loads(request.POST.get('words') or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        return {'status': 'invalid_form'}
    if not isinstance(raw_words, list):
        return {'status': 'invalid_form'}

    from games.ops.workers.jobs.recheck import sync_word_salad_finds
    from games.puzzles.salad.rules import build_ui_context, parse_task_payload

    stats = sync_word_salad_finds(
        task, raw_words,
        team=team, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot,
    )
    grid, words, rares = parse_task_payload(task.checker_data, task.answer or '')
    ui = build_ui_context(grid, words, stats['state'], rare_words=rares)
    result = {
        'status': 'ok',
        'task_id': task.id,
        'word_salad_extras': ui['extra_words'],
        'word_salad_rares': [word['normalized'] for word in ui['rare_words']],
        'word_salad_credited': stats['credited'],
    }
    credited = stats['credited']
    credited_any = bool(credited['extra'] or credited['rare'] or credited['answer'])
    current_mode = game.get_current_mode(Attempt(time=timezone.now(), game=game, task=task))
    ctx = dataclasses.replace(ctx, mode=current_mode)
    effects = apply_move_effects(
        ctx,
        started=credited_any,
        may_complete=bool(credited['answer']) and is_task_completion_state(task, stats['state']),
        source='word_salad',
        observer=request,
    )
    if effects.daily_timing:
        result['daily_timing'] = effects.daily_timing
    if effects.analytics_events:
        result['analytics_events'] = effects.analytics_events
    if effects.replay_available:
        result['replay_available'] = True
    if credited_any:
        update_html = update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
            replay_slot=replay_slot,
        )
        if replay_slot is None:
            track_actor_task_change(
                task,
                team=team,
                update_html=update_html,
                request=request,
                game=game,
                user=user,
                anon_key=anon_key,
                current_mode=current_mode,
                reason=(
                    'attempt.submitted' if credited['answer']
                    else 'word_salad.side_find'
                ),
            )
        result.update(update_html)
    return result


def _raddle_duplicate_response(request, task_id):
    """Duplicate для raddle: синхронизируем UI только если слово уже решено (лаг сети)."""
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {}
    word_index = RaddlePuzzle.posted_word_index(request.POST)
    game = game_from_request_for_task(request, task)
    if game is None:
        return {'raddle_word_index': word_index}
    resolved = resolve_gameplay_actor(
        request, game, gameplay_play_mode(request, game), check_access=False,
    )
    if isinstance(resolved, ActorRefusal):
        return {'raddle_word_index': word_index}
    team = resolved.team
    user = resolved.user
    anon_key = resolved.anon_key
    current_mode = game.get_current_mode(Attempt(time=timezone.now()))
    result = RaddlePuzzle.duplicate_fields(
        task, word_index,
        team=team, user=user, anon_key=anon_key, game=game, current_mode=current_mode,
    )
    if result['raddle_duplicate_solved']:
        result.update(update_task_html(
            request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        ))
    return result


@require_POST
def send_attempt(request, task_id):
    try:
        response = process_send_attempt(request, task_id)
    except DuplicateAttemptException as exc:
        response = {
            'status': 'duplicate',
            'task_id': task_id,
        }
        if getattr(exc, 'attempt_id', None) is not None:
            response['attempt_id'] = exc.attempt_id
        response.update(_raddle_duplicate_response(request, task_id))
    except TooManyAttemptsException:
        response = {'status': 'attempt_limit_exceeded'}
    except ClientEventConflictException:
        response = {'status': 'client_event_conflict'}
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
    with timing_phase(request, 'serialize_response'):
        return JsonResponse(response)
