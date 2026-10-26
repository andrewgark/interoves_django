import json

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.ops.analytics.events import (
    PlayerCompletedGame,
    analytics_game_kind,
    is_task_completion_state,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.daily.completion.completion_coordinator import complete_logical_game
from games.daily.completion import daily_completion_effects_for_game
from games.site.http.exceptions import DuplicateAttemptException, NoGameAccessException
from games.models import Attempt, ChainTaskState, RaddleUiState, Task
from games.auth_observability import log_gameplay_attempt_created
from games.identity.actor import ActorRefusal, resolve_gameplay_actor
from games.puzzles.raddle import RaddlePuzzle
from games.site.session.gameplay_context import context_error_response, validate_gameplay_context
from games.site.session.replay import replay_for_request, StaleReplayError, _official_exists
from games.puzzles.raddle.engine import (
    apply_assist_tier,
    dump_raddle_state,
    ensure_raddle_assist_hints,
    find_raddle_assist_hint,
    load_raddle_state,
    parse_raddle_data,
    raddle_ui_payload,
    serialize_raddle_attempt_text,
)
from games.puzzles.raddle.concurrency import (
    lock_or_create_raddle_state,
    run_raddle_atomic_with_deadlock_retry,
)
from games.middleware.request_timing import timing_phase
from games.realtime.track_compat import track_actor_task_change
from games.site.views.game.context import (
    game_from_request_for_task,
    unpublished_scheduled_task_response,
)
from games.site.views.game.hint import _get_play_mode, create_hint_attempt
from games.site.views.game.render import update_task_html
from games.site.views.pages.util import (
    effective_play_mode,
    get_public_task_or_404,
    has_profile,
    is_browser_form_submission,
    redirect_after_browser_submission,
)


def _chain_state_with_attempt_fallback(row, n_words, team=None, user=None, anon_key=None, task=None, game=None, replay_slot=None):
    """load_raddle_state из CTS; если пусто — из последней Attempt.state (после anon-migrate)."""
    if row.state:
        return load_raddle_state(row.state, n_words)
    attempts = Attempt.manager.get_all_attempts(
        team=team, task=task, user=user, anon_key=anon_key, game=game,
        replay_slot=replay_slot,
    )
    for a in reversed(attempts):
        if a.state:
            return load_raddle_state(a.state, n_words)
    return load_raddle_state(None, n_words)


def _actor_from_request(request, game):
    play_mode = _get_play_mode(request, game)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    resolved = resolve_gameplay_actor(request, game, play_mode)
    if isinstance(resolved, ActorRefusal):
        return None, None, None, resolved.status
    return resolved.team, resolved.user, resolved.anon_key, None


def _reveal_raddle_answer(request, task, game, team, user, anon_key, parsed, word_index, current_mode, replay_slot=None):
    """Тир 2 (💡💡): открыть ответ, послав верную посылку, чтобы слово зачлось."""
    from games.site.views.game.attempt import check_attempt

    n = parsed['n_words']
    attempt = None
    attempt_persisted = False
    # Keep the state mutation and the server-generated correct Attempt in one
    # transaction.  If validation/checking fails, tier 2 must not remain stuck
    # in state without the corresponding solved word.
    with transaction.atomic():
        ChainTaskState.objects.get_or_create(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            replay_slot=replay_slot,
            defaults={'state': None},
        )
        row = ChainTaskState.objects.select_for_update().get(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            replay_slot=replay_slot,
        )
        state = _chain_state_with_attempt_fallback(
            row, n, team=team, user=user, anon_key=anon_key, task=task, game=game,
            replay_slot=replay_slot,
        )
        blocked = RaddlePuzzle.assist_block(state, n, word_index, 2)
        if blocked:
            return {'status': blocked}

        ensure_raddle_assist_hints(task)
        hint = find_raddle_assist_hint(task, word_index, 2)
        if hint is not None:
            try:
                create_hint_attempt(
                    hint, team=team, user=user, anon_key=anon_key, game=game,
                    replay_slot=replay_slot,
                    requested_by_profile=(
                        request.user.profile
                        if request.user.is_authenticated and has_profile(request.user) else None
                    ),
                )
            except DuplicateAttemptException:
                pass

        # Фиксируем тир 2 в state, чтобы посылка ответа зачлась с нулевым кредитом.
        state = apply_assist_tier(state, word_index, 2)
        row.state = json.dumps(dump_raddle_state(state, n), ensure_ascii=False)
        row.save(update_fields=['state', 'updated_at'])

        word = parsed['words'][word_index]
        attempt = Attempt(text=serialize_raddle_attempt_text(word_index, word))
        attempt.team = team
        attempt.user = user
        attempt.anon_key = anon_key
        attempt.task = task
        attempt.time = timezone.now()
        attempt.game = game
        attempt.replay_slot = replay_slot
        try:
            attempt_persisted = check_attempt(
                attempt,
                timing_request=request,
                bypass_attempt_limit=True,
            )
        except DuplicateAttemptException:
            pass

    if attempt_persisted and attempt.pk:
        request.interoves_attempt_id = attempt.pk
        log_gameplay_attempt_created(
            request, attempt=attempt, actor_kind=request.interoves_gameplay_actor_kind,
        )

    result = {
        'status': 'ok',
        'task_id': task.id,
        # Tier 2 creates the successful Attempt on the server itself. Keep
        # this explicit so the client does not infer progress from HTML.
        'raddle_auto_solved': True,
        'raddle_correct': True,
        'raddle_word_index': word_index,
    }
    if attempt_persisted and attempt.pk:
        result['attempt_id'] = attempt.pk
    analytics_events = [] if replay_slot is not None else register_started_game(
        team=team,
        user=user,
        anon_key=anon_key,
        analytics_user=request.user if request.user.is_authenticated else None,
        task=task,
        game=game,
    )
    if (
        analytics_game_kind(game)
        and is_task_completion_state(task, attempt.state)
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
            source='raddle_assist',
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
            if effects['timing']:
                result['daily_timing'] = effects['timing']
            if replay_available:
                result['replay_available'] = True
            analytics_events.extend(effects['analytics_events'])
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
            update_html=update_html,
            request=request,
            game=game,
            user=user,
            anon_key=anon_key,
            current_mode=current_mode,
            reason='raddle.answer_revealed',
        )
    result.update(update_html)
    return result


def process_send_raddle_assist(request, task_id):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {'status': 'invalid'}
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

    team, user, anon_key, err = _actor_from_request(request, game)
    if err:
        return {'status': err}

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
    if replay_slot is None and _official_exists(
        game=game, task_group=task.task_group, team=team, user=user, anon_key=anon_key,
    ):
        return {'status': 'error', 'error': 'replay_required', 'reload_required': True}

    try:
        word_index = int(request.POST.get('word_index', -1))
        tier = int(request.POST.get('tier', 0))
    except (TypeError, ValueError):
        return {'status': 'invalid'}
    if tier not in (1, 2):
        return {'status': 'invalid'}

    parsed = parse_raddle_data(task)
    if not parsed:
        return {'status': 'invalid'}
    if not (parsed.get('assist') or {}).get('enabled', True):
        return {'status': 'disabled'}

    n = parsed['n_words']
    if word_index <= 0 or word_index >= n - 1:
        return {'status': 'invalid'}

    current_mode = game.get_current_mode(Attempt(time=timezone.now()))

    if tier == 2:
        return _reveal_raddle_answer(
            request, task, game, team, user, anon_key,
            parsed, word_index, current_mode, replay_slot,
        )

    with transaction.atomic():
        ChainTaskState.objects.get_or_create(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            replay_slot=replay_slot,
            defaults={'state': None},
        )
        chain_row = ChainTaskState.objects.select_for_update().get(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            replay_slot=replay_slot,
        )
        state = _chain_state_with_attempt_fallback(
            chain_row, n, team=team, user=user, anon_key=anon_key, task=task, game=game,
            replay_slot=replay_slot,
        )
        blocked = RaddlePuzzle.assist_block(state, n, word_index, tier)
        if blocked:
            return {'status': blocked}

        ensure_raddle_assist_hints(task)
        hint = find_raddle_assist_hint(task, word_index, tier)
        if hint is not None:
            try:
                create_hint_attempt(
                    hint, team=team, user=user, anon_key=anon_key, game=game,
                    replay_slot=replay_slot,
                    requested_by_profile=(
                        request.user.profile
                        if request.user.is_authenticated and has_profile(request.user) else None
                    ),
                )
            except DuplicateAttemptException:
                return {'status': 'duplicate'}

        state = apply_assist_tier(state, word_index, tier)
        chain_row.state = json.dumps(dump_raddle_state(state, n), ensure_ascii=False)
        chain_row.save(update_fields=['state', 'updated_at'])

    result = {'status': 'ok', 'task_id': task.id}
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
            update_html=update_html,
            request=request,
            game=game,
            user=user,
            anon_key=anon_key,
            current_mode=current_mode,
            reason='raddle.assist_taken',
        )
    result.update(update_html)
    return result


def _parse_raddle_ui_patch(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _legacy_raddle_ui_state(*, task, game, current_mode, team, user, anon_key, replay_slot, n_words):
    """Read legacy UI keys without locking or writing ChainTaskState."""
    row = ChainTaskState.objects.filter(
        team=team, user=user, anon_key=anon_key,
        task=task, game=game, game_mode=current_mode,
        replay_slot=replay_slot,
    ).only('state').first()
    if not row or not row.state:
        return {'drafts': {}, 'clue_marks': {}}
    legacy = load_raddle_state(row.state, n_words)
    return {
        'drafts': dict(legacy.get('drafts') or {}),
        'clue_marks': dict(legacy.get('clue_marks') or {}),
    }


def _raddle_ui_identity(*, team, user, anon_key, replay_run_id):
    if team is not None:
        actor_key = 'team:{}'.format(team.pk)
    elif user is not None:
        actor_key = 'user:{}'.format(user.pk)
    else:
        actor_key = 'anon:{}'.format(anon_key)
    namespace_key = (
        'replay:{}'.format(replay_run_id)
        if replay_run_id is not None else 'official'
    )
    return actor_key, namespace_key


def process_send_raddle_ui(request, task_id):
    """Collaborative drafts + unused-clue strikethrough. No HTML redraw."""
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {'status': 'invalid'}
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

    team, user, anon_key, err = _actor_from_request(request, game)
    if err:
        return {'status': err}

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
    if replay_slot is None and _official_exists(
        game=game, task_group=task.task_group, team=team, user=user, anon_key=anon_key,
    ):
        return {'status': 'error', 'error': 'replay_required', 'reload_required': True}
    replay_run_id = replay_slot.run_id if replay_slot is not None else None

    drafts_patch = _parse_raddle_ui_patch(request.POST.get('drafts'))
    marks_patch = _parse_raddle_ui_patch(request.POST.get('clue_marks'))
    raw_revision = request.POST.get('ui_revision')
    if raw_revision in (None, ''):
        requested_revision = None
    else:
        try:
            requested_revision = max(0, int(raw_revision))
        except (TypeError, ValueError):
            requested_revision = None
    if drafts_patch is None and marks_patch is None:
        return {'status': 'empty'}

    parsed = parse_raddle_data(task)
    if not parsed:
        return {'status': 'invalid'}
    n = parsed['n_words']
    current_mode = game.get_current_mode(Attempt(time=timezone.now()))
    actor_key, namespace_key = _raddle_ui_identity(
        team=team, user=user, anon_key=anon_key, replay_run_id=replay_run_id,
    )
    state = {}
    stale = False
    revision = 0

    def _save_ui_state():
        nonlocal state, stale, revision
        lookup = {
            'team': team,
            'user': user,
            'anon_key': anon_key,
            'task': task,
            'game': game,
            'game_mode': current_mode,
            'replay_slot': replay_slot,
            'replay_run_id': replay_run_id,
            'actor_key': actor_key,
            'namespace_key': namespace_key,
        }
        # Compute the legacy fallback only after the first-row parent lock. A
        # concurrent answer submission may create ChainTaskState while we wait.
        with timing_phase(request, 'raddle_ui_state_lock'):
            ui_row = lock_or_create_raddle_state(
                queryset=RaddleUiState.objects.filter(**lookup),
                task=task,
                lookup=lookup,
                defaults=lambda: _legacy_raddle_ui_state(
                    task=task,
                    game=game,
                    current_mode=current_mode,
                    team=team,
                    user=user,
                    anon_key=anon_key,
                    replay_slot=replay_slot,
                    n_words=n,
                ),
            )
        accepted = RaddlePuzzle.accept_ui_draft(
            {
                'drafts': dict(ui_row.drafts or {}),
                'clue_marks': dict(ui_row.clue_marks or {}),
            },
            n,
            stored_revision=ui_row.revision,
            requested_revision=requested_revision,
            drafts_patch=drafts_patch,
            clue_marks_patch=marks_patch,
        )
        state = accepted.state
        stale = accepted.stale
        if not stale:
            ui_row.drafts = state['drafts']
            ui_row.clue_marks = state['clue_marks']
            ui_row.updated_by_profile = (
                request.user.profile
                if request.user.is_authenticated and has_profile(request.user) else None
            )
            ui_row.revision = accepted.revision
            ui_row.save(update_fields=[
                'drafts', 'clue_marks', 'updated_by_profile', 'revision', 'updated_at',
            ])
        revision = ui_row.revision

    run_raddle_atomic_with_deadlock_retry(_save_ui_state, label='ui_state')

    payload = {'raddle_ui': raddle_ui_payload(state, task.id, revision=revision)}
    if stale:
        payload['raddle_ui_stale'] = True
    if replay_slot is None:
        track_actor_task_change(
            task,
            team=team,
            update_html=payload,
            game=game,
            user=user,
            anon_key=anon_key,
            current_mode=current_mode,
            reason='raddle.ui_state',
        )
    result = {'status': 'ok', 'task_id': task.id}
    result.update(payload)
    return result


@require_POST
def send_raddle_ui(request, task_id):
    try:
        response = process_send_raddle_ui(request, task_id)
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


@require_POST
def send_raddle_assist(request, task_id):
    try:
        response = process_send_raddle_assist(request, task_id)
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
