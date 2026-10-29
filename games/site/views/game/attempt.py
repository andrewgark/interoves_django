import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.gameplay.attempts import check_attempt  # also re-exported to older importers
from games.gameplay.client_events import parse_client_event_id, receipt_attempt
from games.gameplay.effects import apply_move_effects
from games.gameplay.moves import MoveContext
from games.identity import ActorRefusal, has_profile, resolve_gameplay_actor
from games.models import CHAIN_TASK_TYPES, Attempt
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
from games.site.http.auth_observability import log_gameplay_attempt_created
from games.site.http.exceptions import (
    ClientEventConflictException,
    DuplicateAttemptException,
    InvalidFormException,
    NoGameAccessException,
    ReplayedClientEventException,
    TooManyAttemptsException,
)
from games.site.http.middleware.request_timing import timing_phase
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


def _raddle_stale_submit_response(request, task, team, user, anon_key, game, current_mode, word_index, replay_slot=None):
    """
    Устаревший UI (bfcache / смена anon↔login): форма на некрайнем или уже
    решённом слове. Не пишем Attempt — только синхронизируем HTML.
    """
    result = RaddlePuzzle.stale_payload(
        task, team, user, anon_key, game, current_mode, word_index, replay_slot,
    )
    if result is None:
        return None
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


def get_first_new_hint_actor(task, team=None, user=None, anon_key=None, replay_slot=None):
    from games.models import Hint, HintAttempt
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.key_sort())
    for hint in hints:
        if team is not None:
            exists = HintAttempt.objects.filter(
                team=team, user__isnull=True, anon_key__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        elif user is not None:
            exists = HintAttempt.objects.filter(
                user=user, team__isnull=True, anon_key__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        else:
            exists = HintAttempt.objects.filter(
                anon_key=anon_key, team__isnull=True, user__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        if not exists:
            return hint
    return None


def _get_play_mode(request, game):
    # The mode is stored in the session for navigation, but a session is shared
    # by all tabs.  Keep a page's in-flight submissions on the mode with which
    # that page was rendered; the signed gameplay context is still validated
    # below and independently binds the request to the current actor.
    mode = request.headers.get('X-Interoves-Play-Mode')
    if mode not in ('team', 'personal'):
        mode = request.session.get('play_mode_{}'.format(game.project_id or 'main'))
    if mode not in ('team', 'personal'):
        mode = 'personal' if game.project_id == 'sections' else 'team'
    return effective_play_mode(mode, game, user=request.user)


def process_send_attempt(request, task_id):
    task = get_public_task_or_404(task_id)

    game = game_from_request_for_task(request, task)
    if game is None:
        return {'status': 'ambiguous_game'}
    unpublished = unpublished_scheduled_task_response(request, game, task)
    if unpublished is not None:
        return unpublished
    play_mode = _get_play_mode(request, game)
    resolved = resolve_gameplay_actor(request, game, play_mode)
    if isinstance(resolved, ActorRefusal):
        return {'status': resolved.status}
    team = resolved.team
    user = resolved.user
    anon_key = resolved.anon_key

    from games.club.access.archive import user_can_access_task_archive

    if not user_can_access_task_archive(
        request.user, game, task, offer_share=request.POST.get('offer_share'),
    ):
        raise NoGameAccessException('Club subscription required for archived game')

    context_error = validate_gameplay_context(
        request, task=task, game=game, team=team, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error

    try:
        replay_slot = replay_for_request(
            request=request,
            game=game,
            task_group=task.task_group,
            team=team,
            user=user,
            anon_key=anon_key,
        )
    except StaleReplayError:
        return {'status': 'error', 'error': 'stale_replay', 'reload_required': True}
    is_game_start_interaction = False
    word_index = None
    word = None
    post_puzzle = get_puzzle(task.task_type)
    post_reader = getattr(post_puzzle, 'read_post', None) if post_puzzle is not None else None
    if post_reader is None and task.task_type == 'alphabetty':
        post_reader = AlphabettyPuzzle.read_post
    if post_reader is None and task.task_type == 'grid-puzzle':
        post_reader = GridPuzzle.read_post
    if task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint', 'proportions'):
        form = AttemptForm(request.POST)
        if not form.is_valid():
            raise InvalidFormException('attempt form {} is not valid'.format(form))

        attempt = form.save(commit=False)
    elif task.task_type in POSTED_TEXT_TASK_TYPES:
        move = parse_posted_text(task.task_type, request.POST)
        if isinstance(move, MoveRefusal):
            return {'status': move.status}
        attempt = Attempt(text=move.text)
    elif post_reader is not None:
        move = post_reader(request.POST, task)
        status = getattr(move, 'status', None)
        if status is not None:
            return {'status': status}
        if isinstance(move, SaladSync):
            return _process_word_salad_sync_finds(
                request, task, team, user, anon_key, game, replay_slot=replay_slot,
            )
        attempt = Attempt(text=move.text if hasattr(move, 'text') else move.word)
        word_index = getattr(move, 'word_index', None)
        if word_index is not None:
            word = move.word
        action = getattr(move, 'action', None)
        is_game_start_interaction = bool(getattr(move, 'starts_game', False))
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.user = user
    attempt.anon_key = anon_key
    attempt.task = task
    attempt.time = timezone.now()
    attempt.game = game
    attempt.replay_slot = replay_slot

    # A resent move answers with the attempt it already produced, before any
    # check that the first delivery itself may have changed (e.g. a ladder
    # word that is no longer playable because it was just solved).
    client_event_id = parse_client_event_id(request.POST)
    if client_event_id is not None:
        replayed = receipt_attempt(client_event_id, attempt)
        if replayed is not None:
            return _replayed_move_response(
                request, task, game, replayed,
                team=team, user=user, anon_key=anon_key, word_index=word_index, word=word,
            )

    if replay_slot is None:
        from games.site.session.replay import _official_exists
        if _official_exists(
            game=game,
            task_group=task.task_group,
            team=team,
            user=user,
            anon_key=anon_key,
        ):
            duplicate = Attempt.manager.filter(
                task=task,
                game=game,
                team=team,
                user=user,
                anon_key=anon_key,
                replay_slot=None,
                text=attempt.text,
            ).exists()
            if not duplicate:
                return {'status': 'error', 'error': 'replay_required', 'reload_required': True}

    current_mode = game.get_current_mode(attempt)

    stale_for = getattr(post_puzzle, 'stale_payload', None) if post_puzzle is not None else None
    if stale_for is not None and word_index is not None:
        stale = _raddle_stale_submit_response(
            request, task, team, user, anon_key, game, current_mode, word_index, replay_slot,
        )
        if stale is not None:
            return stale

    keeper = getattr(post_puzzle, 'persist_wrong', None) if post_puzzle is not None else None
    persist_wrong = keeper(request.POST, action) if keeper is not None else True
    with timing_phase(request, 'check_attempt'):
        try:
            attempt_persisted = check_attempt(
                attempt,
                persist_wrong=persist_wrong,
                timing_request=request,
                client_event_id=client_event_id,
            )
        except ReplayedClientEventException as exc:
            return _replayed_move_response(
                request, task, game, Attempt.manager.get(pk=exc.attempt_id),
                team=team, user=user, anon_key=anon_key, word_index=word_index, word=word,
            )
    if attempt_persisted:
        request.interoves_attempt_id = attempt.pk
        with timing_phase(request, 'attempt_created_log'):
            log_gameplay_attempt_created(
                request,
                attempt=attempt,
                actor_kind=request.interoves_gameplay_actor_kind,
            )

    if attempt_persisted and task.task_type == 'autohint':
        from games.puzzles.markers.autohint import AutohintPuzzle
        if AutohintPuzzle.opens_on_wrong(attempt):
            with timing_phase(request, 'autohint'):
                hint = get_first_new_hint_actor(
                    task, team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
                )
                if hint is not None:
                    from games.site.views.game.hint import create_hint_attempt
                    create_hint_attempt(
                        hint, team=team, user=user, anon_key=anon_key,
                        game=game, replay_slot=replay_slot,
                        requested_by_profile=(
                            request.user.profile
                            if request.user.is_authenticated and has_profile(request.user) else None
                        ),
                    )

    ctx = MoveContext(
        task=task, game=game, team=team, user=user, anon_key=anon_key,
        replay_slot=replay_slot, mode=current_mode,
        analytics_user=request.user if request.user.is_authenticated else None,
        run_id=getattr(request, 'interoves_replay_run_id', None),
    )
    effects = apply_move_effects(
        ctx,
        started=bool(attempt_persisted or is_game_start_interaction),
        may_complete=bool(attempt_persisted) and (
            attempt.status == 'Ok'
            or task.task_type in CHAIN_TASK_TYPES
            or is_task_completion_state(task, attempt.state)
        ),
        source='send_attempt',
        observer=request,
    )

    with timing_phase(request, 'response_payload'):
        result = {
            'status': 'ok',
            'task_id': task.id,
        }
    if effects.daily_timing:
        result['daily_timing'] = effects.daily_timing
    if effects.replay_available:
        result['replay_available'] = True
    if effects.analytics_events:
        result['analytics_events'] = effects.analytics_events
    return _move_response(
        request, result, task=task, game=game, attempt=attempt,
        attempt_persisted=bool(attempt_persisted),
        team=team, user=user, anon_key=anon_key, current_mode=current_mode,
        replay_slot=replay_slot, word_index=word_index, word=word,
    )


def _replayed_move_response(request, task, game, attempt, *, team, user, anon_key, word_index, word):
    """Answer a resent move with the attempt its first delivery produced."""
    result = {
        'status': 'ok',
        'task_id': task.id,
        'client_event_replayed': True,
    }
    return _move_response(
        request, result, task=task, game=game, attempt=attempt, attempt_persisted=True,
        team=team, user=user, anon_key=anon_key, current_mode=game.get_current_mode(attempt),
        replay_slot=attempt.replay_slot, word_index=word_index, word=word,
    )


def _move_response(
    request, result, *, task, game, attempt, attempt_persisted,
    team, user, anon_key, current_mode, replay_slot, word_index, word,
):
    """Add the verdict, puzzle fields and task HTML to a processed move."""
    # The transport status above only says that the submission was processed.
    # Give the new UI the persisted verdict as well, so ordinary tasks can show
    # explicit feedback without trying to infer it from freshly rendered HTML.
    # Raddle keeps its own contract and does not add attempt_status.
    puzzle = get_puzzle(task.task_type)
    if attempt_persisted and getattr(puzzle, 'report_attempt_status', True):
        result['attempt_status'] = attempt.status
        result['attempt_id'] = attempt.id
    response = MoveResponse(
        result=result,
        attempt=attempt,
        attempt_persisted=attempt_persisted,
        user=user,
        anon_key=anon_key,
        word_index=word_index,
        word=word,
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
                request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
                replay_slot=replay_slot,
            )
        if replay_slot is None:
            with timing_phase(request, 'schedule_realtime'):
                track_actor_task_change(
                    task,
                    team=team,
                    update_html=update_html,
                    request=request,
                    game=game,
                    user=user,
                    anon_key=anon_key,
                    current_mode=current_mode,
                    reason='attempt.submitted',
                )
        result.update(update_html)
    return result


def _process_word_salad_sync_finds(request, task, team, user, anon_key, game, replay_slot=None):
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
    ctx = MoveContext(
        task=task, game=game, team=team, user=user, anon_key=anon_key,
        replay_slot=replay_slot, mode=current_mode,
        analytics_user=request.user if request.user.is_authenticated else None,
        run_id=getattr(request, 'interoves_replay_run_id', None),
    )
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
    play_mode = _get_play_mode(request, game)
    resolved = resolve_gameplay_actor(
        request, game, play_mode, check_access=False,
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
