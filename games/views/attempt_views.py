import json

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from games.analytics import (
    PlayerCompletedGame,
    analytics_game_kind,
    is_task_completion_state,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.check import CheckerFactory
from games.exception import DuplicateAttemptException, TooManyAttemptsException, InvalidFormException, NoGameAccessException
from games.forms import AttemptForm
from games.models import Attempt, ChainTaskState, CheckerType, GameTaskGroup, Task, Team, CHAIN_TASK_TYPES
from games.replay import StaleReplayError, replay_for_request
from games.completion_coordinator import complete_logical_game
from games.daily.completion import daily_completion_effects_for_game
from games.middleware.request_timing import timing_phase
from games.auth_observability import log_gameplay_attempt_created
from games.identity.actor import ActorRefusal, resolve_gameplay_actor
from games.gameplay_context import context_error_response, validate_gameplay_context
from games.views.util import (
    effective_play_mode,
    get_public_task_or_404,
    has_profile,
    is_browser_form_submission,
    redirect_after_browser_submission,
)
from games.grid_puzzle import grid_checker_id
from games.views.game_context import (
    game_from_request_for_task,
    unpublished_scheduled_task_response,
)
from games.views.render_task import update_task_html
from games.views.track import track_actor_task_change
from games.puzzles import POSTED_TEXT_TASK_TYPES, get_puzzle, parse_posted_text
from games.puzzles.grid import GridPuzzle
from games.puzzles.quota import revision_quota_message
from games.puzzles.alphabetty import AlphabettyMoveRefusal, AlphabettyPuzzle
from games.puzzles.move import MoveRefusal
from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.raddle_submission import RaddleMoveRefusal
from games.puzzles.word_salad import WordSaladPuzzle
from games.puzzles.word_salad_submission import SaladMoveRefusal, SaladSync
from games.raddle_concurrency import (
    lock_or_create_raddle_state,
    run_raddle_atomic_with_deadlock_retry,
)


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


def check_attempt(
    attempt,
    *,
    persist_wrong=True,
    timing_request=None,
    bypass_attempt_limit=False,
):
    """Check and optionally persist an attempt.

    ``bypass_attempt_limit`` is reserved for server-generated successful
    attempts (currently Raddle's answer assist).  It does not bypass duplicate
    detection or any checker/state validation; it only allows that authoritative
    answer to be persisted after the player's typo quota is exhausted.
    """
    task = attempt.task
    request_user = getattr(timing_request, 'user', None)
    if (
        attempt.submitted_by_profile_id is None
        and request_user is not None
        and getattr(request_user, 'is_authenticated', False)
    ):
        # The gameplay actor may be the whole team; retain the submitting
        # member separately from Attempt.user, which owns personal progress.
        attempt.submitted_by_profile_id = request_user.pk
    team = attempt.team
    user = getattr(attempt, 'user', None)
    anon_key = getattr(attempt, 'anon_key', None)
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for attempt (set Attempt.game or use a single-linked task group)')
    current_mode = game.get_current_mode(attempt)
    if attempt._state.adding and attempt.task_revision is None:
        attempt.task_revision = task.attempt_revision
    attempt_revision = attempt.task_revision or task.attempt_revision
    modes = ['general']
    if current_mode == 'tournament':
        modes.append('tournament')

    is_chain_task = task.task_type in CHAIN_TASK_TYPES
    puzzle = get_puzzle(task.task_type)

    def _run():
        last_attempt_state = None
        chain_state_row = None

        # Ordinary tasks need the Task row as a stable lock so concurrent
        # submissions cannot both pass duplicate/limit checks.  Chain tasks
        # have a narrower actor-specific ChainTaskState lock below; taking the
        # global Task lock as well makes every teammate block every other
        # teammate on the same ladder, and can end in MySQL lock wait timeout
        # (especially while raddle.ui_state is saving drafts).
        if attempt._state.adding and not is_chain_task:
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            attempt.time = timezone.now()
        elif attempt._state.adding:
            attempt.time = timezone.now()

        if is_chain_task:
            # First-row creation is serialized on Task because MySQL does not
            # materialize Django's conditional UNIQUE constraints here.
            if team is not None:
                chain_actor_key = 'team:{}'.format(team.pk)
            elif user is not None:
                chain_actor_key = 'user:{}'.format(user.pk)
            else:
                chain_actor_key = 'anon:{}'.format(anon_key)
            chain_lookup = {
                'team': team,
                'user': user,
                'anon_key': anon_key,
                'task': task,
                'game': game,
                'game_mode': current_mode,
                'replay_slot': attempt.replay_slot,
                'replay_slot_key': attempt.replay_slot_id or 0,
                'actor_key': chain_actor_key,
            }
            with timing_phase(timing_request, 'chain_state_ensure'):
                chain_state_row = lock_or_create_raddle_state(
                    queryset=ChainTaskState.objects.filter(**chain_lookup),
                    task=task,
                    lookup=chain_lookup,
                    defaults={'state': None},
                )
            with timing_phase(timing_request, 'chain_state_lock'):
                # The helper returns the row locked by primary key. Keep this
                # phase for timing compatibility with existing observability.
                chain_state_row = ChainTaskState.objects.select_for_update().get(pk=chain_state_row.pk)
            last_attempt_state = chain_state_row.state

        for mode in modes:
            attempts = Attempt.manager.get_attempts_before(
                team, task, attempt.time, mode, user=user, anon_key=anon_key, game=game,
                replay_slot=attempt.replay_slot,
            )
            revision_attempts = [
                previous for previous in attempts
                if previous.task_revision == attempt_revision
            ]

            # Пустой ChainTaskState после anon-migrate: подтянуть state из попыток
            # текущего режима (не general→tournament — иначе ломается изоляция).
            if (
                is_chain_task
                and last_attempt_state is None
                and mode == current_mode
                and attempts
            ):
                for prev in reversed(attempts):
                    if prev.state:
                        last_attempt_state = prev.state
                        break
            elif not is_chain_task and mode == 'general' and attempts:
                if last_attempt_state is None:
                    last_attempt_state = attempts[-1].state

            if mode == 'tournament' and not bypass_attempt_limit:
                if puzzle is not None:
                    message = puzzle.attempt_limit_message(
                        attempt, revision_attempts, task, team,
                        attempts=attempts,
                        chain_state=chain_state_row.state if chain_state_row else None,
                    )
                else:
                    message = revision_quota_message(attempt, revision_attempts, task, team)
                if message:
                    raise TooManyAttemptsException(message)

            for other_attempt in revision_attempts:
                if puzzle is not None:
                    verdict = puzzle.blocks_duplicate(
                        attempt.text, other_attempt.text,
                        task=task, state_raw=last_attempt_state,
                        other_attempt=other_attempt,
                    )
                    if verdict.blocked:
                        exc = DuplicateAttemptException(
                            'Attempt duplicates one of the previous attempts by this team'
                        )
                        if verdict.attempt_id is not None:
                            exc.attempt_id = verdict.attempt_id
                        raise exc
                elif attempt.text == other_attempt.text:
                    exc = DuplicateAttemptException(
                        'Attempt duplicates one of the previous attempts by this team'
                    )
                    exc.attempt_id = other_attempt.id
                    raise exc

        checker_type = task.get_checker()
        if puzzle is not None and puzzle.checker_id:
            checker_type = CheckerType.objects.get(id=puzzle.checker_id)
        if task.task_type == 'grid-puzzle':
            checker_id = grid_checker_id(task)
            checker_type = CheckerType.objects.get(id=checker_id)
        checker_data = task.checker_data or ''
        # equals / equals_with_possible_spaces читают эталон из checker_data; для «Пропорций»
        # и обычных заданий ответ часто задают только в answer — тогда дублируем его сюда.
        if checker_type.id in ('equals', 'equals_with_possible_spaces'):
            if not checker_data.strip() and (task.answer or '').strip():
                checker_data = task.answer
        checker = CheckerFactory().create_checker(checker_type, checker_data, last_attempt_state)
        check_result = checker.check(attempt.text, attempt)
        attempt.status, attempt.points, attempt.state, attempt.comment = check_result.status, check_result.points, check_result.state, check_result.comment
        # Recompute the hidden checker result on every check; do not retain a
        # stale value after a replay or a checker change.
        attempt.possible_status = None
        if 'tournament' in modes and attempt.status != 'Ok':
            attempt.possible_status = attempt.status
            attempt.status = check_result.tournament_status
        from decimal import Decimal
        scaler = getattr(puzzle, 'scale_points', None) if puzzle is not None else None
        if scaler is not None:
            attempt.points = scaler(task, attempt.points)
        else:
            attempt.points = Decimal(str(attempt.points or 0)) * task.get_points()

        # Store a server-derived cumulative active clock only for an answer
        # that advanced the puzzle. Hint rows and wrong/duplicate submissions
        # must never become per-answer observations.
        if attempt.status in ('Ok', 'Partial') and task.task_type in ('raddle', 'word_salad', 'alphabetty'):
            advanced = task.task_type == 'alphabetty' and attempt.status == 'Ok'
            if task.task_type in ('raddle', 'word_salad'):
                try:
                    before = json.loads(last_attempt_state or '{}')
                    after = json.loads(attempt.state or '{}')
                except (TypeError, ValueError):
                    before, after = {}, {}
                key = 'solved_indices'
                advanced = bool(set(after.get(key) or []) - set(before.get(key) or []))
            if advanced:
                from games.daily_timing import active_time_ms_for_attempt
                attempt.active_time_ms = active_time_ms_for_attempt(
                    game=game,
                    task_group=task.task_group,
                    user=user,
                    anon_key=anon_key,
                    team=team,
                    replay_slot=attempt.replay_slot,
                    now=attempt.time,
                )

        # Auto-checking controls may probe a candidate without turning every typo
        # into an Attempt. The checker still runs under the chain-state lock, but
        # only a result that advances the task is committed.
        if not persist_wrong and check_result.status in ('Wrong', 'Pending'):
            return False

        attempt.save()

        if chain_state_row is not None:
            chain_state_row.state = attempt.state
            chain_state_row.last_attempt = attempt
            chain_state_row.save(update_fields=['state', 'last_attempt', 'updated_at'])
        return True

    # Chain submissions can deadlock with the collaborative UI state writer.
    # Retry the complete transaction so MySQL has rolled back every lock held
    # by the failed attempt before we evaluate the answer again.
    if is_chain_task:
        persisted = run_raddle_atomic_with_deadlock_retry(
            _run, label='check_attempt',
        )
    else:
        # The Task row is the stable lock row for ordinary submissions too.
        with transaction.atomic():
            persisted = _run()

    if not persisted:
        return False

    # if some task had tag on this task, recheck it too
    if task.task_type == 'with_tag':
        tag_task_number = task.tags.get('task')
        tag_team_name = task.tags.get('team')
        if tag_task_number is None or tag_team_name is None:
            return True
        try:
            tag_task = Task.objects.visible().get(
                task_group=task.task_group, checker_data__contains=tag_task_number,
            )
            tag_team = Team.objects.get(name=tag_team_name)
            assert tag_task.task_type != 'with_tag'
        except Exception:
            return True

        tag_attempts = Attempt.manager.filter(task=tag_task, team=tag_team, game=game)
        rechecked = False
        for tag_attempt in tag_attempts:
            check_attempt(tag_attempt, timing_request=timing_request)
            rechecked = True
        # Attempt.save() deliberately has no broadcast side effect.  Notify the
        # affected team once after the dependent task has been fully rechecked.
        if rechecked:
            track_actor_task_change(
                tag_task,
                team=tag_team,
                game=game,
                reason='task.dependency_rechecked',
            )
    return True


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

    from games.club_access import user_can_access_task_archive

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
    elif task.task_type == 'raddle':
        move = RaddlePuzzle.parse_move(request.POST)
        if isinstance(move, RaddleMoveRefusal):
            return {'status': move.status}
        word_index = move.word_index
        word = move.word
        attempt = Attempt(text=move.text)
    elif task.task_type == 'alphabetty':
        move = AlphabettyPuzzle.parse_move(request.POST)
        if isinstance(move, AlphabettyMoveRefusal):
            return {'status': move.status}
        attempt = Attempt(text=move.word)
    elif task.task_type == 'word_salad':
        move = WordSaladPuzzle.parse_move(request.POST)
        if isinstance(move, SaladMoveRefusal):
            return {'status': move.status}
        if isinstance(move, SaladSync):
            return _process_word_salad_sync_finds(
                request, task, team, user, anon_key, game, replay_slot=replay_slot,
            )
        attempt = Attempt(text=move.text)
        action = move.action
        is_game_start_interaction = move.starts_game
    elif task.task_type == 'grid-puzzle':
        move = GridPuzzle.parse_move(request.POST, task)
        if isinstance(move, MoveRefusal):
            return {'status': move.status}
        attempt = Attempt(text=move.text)
    else:
        raise Exception('Unknown task_type: {}'.format(task.task_type))
    attempt.team = team
    attempt.user = user
    attempt.anon_key = anon_key
    attempt.task = task
    attempt.time = timezone.now()
    attempt.game = game
    attempt.replay_slot = replay_slot

    if replay_slot is None:
        from games.replay import _official_exists
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

    if task.task_type == 'raddle':
        stale = _raddle_stale_submit_response(
            request, task, team, user, anon_key, game, current_mode, word_index, replay_slot,
        )
        if stale is not None:
            return stale

    correct_only = (
        task.task_type == 'word_salad'
        and action == 'solve'
        and request.POST.get('correct_only') == '1'
    )
    with timing_phase(request, 'check_attempt'):
        attempt_persisted = check_attempt(
            attempt,
            persist_wrong=not correct_only,
            timing_request=request,
        )
    if attempt_persisted:
        request.interoves_attempt_id = attempt.pk
        with timing_phase(request, 'attempt_created_log'):
            log_gameplay_attempt_created(
                request,
                attempt=attempt,
                actor_kind=request.interoves_gameplay_actor_kind,
            )

    if attempt_persisted and task.task_type == 'autohint' and attempt.status in ('Pending', 'Wrong'):
        with timing_phase(request, 'autohint'):
            hint = get_first_new_hint_actor(
                task, team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
            )
            if hint is not None:
                from games.views.hint_views import create_hint_attempt
                create_hint_attempt(
                    hint, team=team, user=user, anon_key=anon_key,
                    game=game, replay_slot=replay_slot,
                    requested_by_profile=(
                        request.user.profile
                        if request.user.is_authenticated and has_profile(request.user) else None
                    ),
                )

    analytics_events = []
    daily_timing = None
    replay_available = False
    if (attempt_persisted or is_game_start_interaction) and replay_slot is None:
        with timing_phase(request, 'analytics_started'):
            analytics_events.extend(register_started_game(
                team=team,
                user=user,
                anon_key=anon_key,
                analytics_user=request.user if request.user.is_authenticated else None,
                task=task,
                game=game,
            ))
    completion_ready = False
    if attempt_persisted and analytics_game_kind(game) and (
        attempt.status == 'Ok'
        or task.task_type in CHAIN_TASK_TYPES
        or is_task_completion_state(task, attempt.state)
    ):
        with timing_phase(request, 'completion_check'):
            completion_ready = is_task_group_complete(
                task_group=task.task_group,
                game=game,
                team=team,
                user=user,
                anon_key=anon_key,
                mode=current_mode,
                replay_slot=replay_slot,
            )
    if completion_ready:
        completion = complete_logical_game(
            actor={'team': team, 'user': user, 'anon_key': anon_key},
            game=game,
            task_group=task.task_group,
            task=task,
            replay_slot=replay_slot,
            run_id=getattr(request, 'interoves_replay_run_id', None),
            analytics_user=request.user if request.user.is_authenticated else None,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=current_mode,
            source='send_attempt',
        )
        if completion is not None:
            replay_available = replay_slot is None
            if replay_available:
                with timing_phase(request, 'analytics_completed'):
                    effects = daily_completion_effects_for_game(
                        game.id,
                        completion,
                        replay_slot=replay_slot,
                        publish_analytics=publish_completion_analytics,
                        analytics_kwargs={
                            'record': completion['record'],
                            'created': completion['created'],
                            'user': user,
                            'anon_key': anon_key,
                            'analytics_user': request.user if request.user.is_authenticated else None,
                            'game': game,
                            'task_group': task.task_group,
                        },
                    )
                daily_timing = effects['timing']
            else:
                effects = daily_completion_effects_for_game(
                    game.id,
                    completion,
                    replay_slot=replay_slot,
                    publish_analytics=publish_completion_analytics,
                    analytics_kwargs={},
                )
            analytics_events.extend(effects['analytics_events'])

    with timing_phase(request, 'response_payload'):
        result = {
            'status': 'ok',
            'task_id': task.id,
        }
    if daily_timing:
        result['daily_timing'] = daily_timing
    if replay_available:
        result['replay_available'] = True
    # The transport status above only says that the submission was processed.
    # Give the new UI the persisted verdict as well, so ordinary tasks can show
    # explicit feedback without trying to infer it from freshly rendered HTML.
    # Keep raddle on its dedicated response contract (raddle_correct / needs_sync).
    if attempt_persisted and task.task_type != 'raddle':
        result['attempt_status'] = attempt.status
        result['attempt_id'] = attempt.id
    if analytics_events:
        result['analytics_events'] = analytics_events
    if task.task_type == 'raddle':
        RaddlePuzzle.write_move_result(result, task, attempt, word_index, word)
    if task.task_type == 'word_salad':
        WordSaladPuzzle.write_move_result(
            result, task, attempt,
            attempt_persisted=attempt_persisted, user=user, anon_key=anon_key,
        )
    if task.task_type == 'grid-puzzle':
        result['grid_puzzle_correct'] = attempt.status == 'Ok'

    # Raddle wrong answers: client updates locally (showRaddleWrongFeedback); skip ~40KB HTML.
    need_task_html = (
        task.task_type != 'raddle' or RaddlePuzzle.include_task_html(result)
    ) and (
        task.task_type != 'word_salad' or WordSaladPuzzle.include_task_html(result)
    )
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

    from games.recheck import sync_word_salad_finds
    from games.word_salad import build_ui_context, parse_task_payload

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
    analytics_events = []
    daily_timing = None
    replay_available = False
    if credited_any and replay_slot is None:
        analytics_events.extend(register_started_game(
            team=team,
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        ))
    if (
        credited['answer']
        and analytics_game_kind(game)
        and is_task_completion_state(task, stats['state'])
        and is_task_group_complete(
            task_group=task.task_group,
            game=game,
            team=team,
            user=user,
            anon_key=anon_key,
            mode=current_mode,
            replay_slot=replay_slot,
        )
    ):
        completion = complete_logical_game(
            actor={'team': team, 'user': user, 'anon_key': anon_key},
            game=game,
            task_group=task.task_group,
            task=task,
            replay_slot=replay_slot,
            run_id=getattr(request, 'interoves_replay_run_id', None),
            analytics_user=request.user if request.user.is_authenticated else None,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=current_mode,
            source='word_salad',
        )
        if completion is not None:
            replay_available = replay_slot is None
            effects = daily_completion_effects_for_game(
                game.id,
                completion,
                replay_slot=replay_slot,
                publish_analytics=publish_completion_analytics,
                analytics_kwargs={
                    'record': completion['record'],
                    'created': completion['created'],
                    'user': user,
                    'anon_key': anon_key,
                    'analytics_user': request.user if request.user.is_authenticated else None,
                    'game': game,
                    'task_group': task.task_group,
                },
            )
            daily_timing = effects['timing']
            analytics_events.extend(effects['analytics_events'])
    if daily_timing:
        result['daily_timing'] = daily_timing
    if analytics_events:
        result['analytics_events'] = analytics_events
    if replay_available:
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
