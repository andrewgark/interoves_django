import json

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from games.analytics import (
    PlayerCompletedGame,
    is_task_completion_state,
    is_task_group_complete,
    register_completed_game,
    register_started_game,
    supported_game_kind,
)
from games.exception import DuplicateAttemptException, NoGameAccessException
from games.models import Attempt, ChainTaskState, Task
from games.raddle import (
    apply_assist_tier,
    dump_raddle_state,
    ensure_raddle_assist_hints,
    find_raddle_assist_hint,
    load_raddle_state,
    merge_raddle_ui_state,
    parse_raddle_data,
    playable_word_indices,
    raddle_ui_payload,
    resolve_assist_tiers,
    serialize_raddle_attempt_text,
)
from games.views.game_context import game_from_request_for_task
from games.views.hint_views import _get_play_mode, create_hint_attempt
from games.views.render_task import update_task_html
from games.views.track import track_actor_task_change
from games.views.util import effective_play_mode, get_public_task_or_404, has_profile, has_team
from games.analytics_identity import gameplay_anon_key
from games.auth_observability import log_gameplay_attempt_created
from games.gameplay_context import context_error_response, validate_gameplay_context
from games.replay import replay_for_request, StaleReplayError, _official_exists


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
    team = user = anon_key = None
    if play_mode == 'team':
        if not request.user.is_authenticated or not has_team(request.user):
            return None, None, None, 'no_team'
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            raise NoGameAccessException('User has no access to game {}'.format(game))
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                return None, None, None, 'no_profile'
            user = request.user
        else:
            anon_key = gameplay_anon_key(request)
            if not anon_key:
                return None, None, None, 'no_anon'
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise NoGameAccessException('User has no access to game {}'.format(game))
    return team, user, anon_key, None


def _reveal_raddle_answer(request, task, game, team, user, anon_key, parsed, word_index, current_mode, replay_slot=None):
    """Тир 2 (💡💡): открыть ответ, послав верную посылку, чтобы слово зачлось."""
    from games.views.attempt_views import check_attempt

    n = parsed['n_words']
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
        if word_index in set(state.get('solved_indices') or []):
            return {'status': 'already_solved'}
        if word_index not in playable_word_indices(state, n):
            return {'status': 'not_playable'}
        if resolve_assist_tiers(state).get(word_index, 0) < 1:
            return {'status': 'need_clue_first'}

        ensure_raddle_assist_hints(task)
        hint = find_raddle_assist_hint(task, word_index, 2)
        if hint is not None:
            try:
                create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
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
        check_attempt(attempt)
    except DuplicateAttemptException:
        pass

    if attempt.pk:
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
    if attempt.pk:
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
        supported_game_kind(game)
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
        if replay_slot is not None:
            from games.replay import mark_replay_completed
            mark_replay_completed(replay_slot)
        else:
            analytics_events.extend(register_completed_game(
            team=team,
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=current_mode,
        ))
            from games.daily_timing import complete_daily_timing
            timing = complete_daily_timing(
            game=game,
            task_group=task.task_group,
            user=user,
            anon_key=anon_key,
            team=team,
            )
        if timing:
            result['daily_timing'] = timing
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
        if word_index in set(state.get('solved_indices') or []):
            return {'status': 'already_solved'}
        playable = playable_word_indices(state, n)
        if word_index not in playable:
            return {'status': 'not_playable'}

        assist_tiers = resolve_assist_tiers(state)
        current_tier = assist_tiers.get(word_index, 0)
        if current_tier >= 1:
            return {'status': 'duplicate'}

        ensure_raddle_assist_hints(task)
        hint = find_raddle_assist_hint(task, word_index, tier)
        if hint is not None:
            try:
                create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
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


def process_send_raddle_ui(request, task_id):
    """Collaborative drafts + unused-clue strikethrough. No HTML redraw."""
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        return {'status': 'invalid'}
    game = game_from_request_for_task(request, task)
    if game is None:
        return {'status': 'ambiguous_game'}

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

    drafts_patch = _parse_raddle_ui_patch(request.POST.get('drafts'))
    marks_patch = _parse_raddle_ui_patch(request.POST.get('clue_marks'))
    if drafts_patch is None and marks_patch is None:
        return {'status': 'empty'}

    parsed = parse_raddle_data(task)
    if not parsed:
        return {'status': 'invalid'}
    n = parsed['n_words']
    current_mode = game.get_current_mode(Attempt(time=timezone.now()))

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
        state = merge_raddle_ui_state(
            state, n, drafts_patch=drafts_patch, clue_marks_patch=marks_patch,
        )
        chain_row.state = json.dumps(state, ensure_ascii=False)
        chain_row.save(update_fields=['state', 'updated_at'])

    payload = {'raddle_ui': raddle_ui_payload(state, task.id)}
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


@require_http_methods(['POST'])
def send_raddle_ui(request, task_id):
    try:
        response = process_send_raddle_ui(request, task_id)
    except NoGameAccessException:
        response = {'status': 'no_access'}
    context_response = context_error_response(response)
    if context_response is not None:
        return context_response
    return JsonResponse(response)


@require_http_methods(['POST'])
def send_raddle_assist(request, task_id):
    try:
        response = process_send_raddle_assist(request, task_id)
    except NoGameAccessException:
        response = {'status': 'no_access'}
    context_response = context_error_response(response)
    if context_response is not None:
        return context_response
    return JsonResponse(response)
