import json

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from games.exception import DuplicateAttemptException, NoGameAccessException
from games.models import Attempt, ChainTaskState, Task
from games.raddle import (
    apply_assist_tier,
    ensure_raddle_assist_hints,
    find_raddle_assist_hint,
    load_raddle_state,
    parse_raddle_data,
    playable_word_indices,
    resolve_assist_tiers,
)
from games.views.game_context import game_from_request_for_task
from games.views.hint_views import _get_play_mode, create_hint_attempt
from games.views.render_task import update_task_html
from games.views.track import track_task_change
from games.views.util import effective_play_mode, get_public_task_or_404, has_profile, has_team


def _actor_from_request(request, game):
    play_mode = _get_play_mode(request, game)
    play_mode = effective_play_mode(play_mode, game)
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
            anon_key = request.POST.get('anon_key') or request.headers.get('X-Interoves-Anon')
            if not anon_key:
                return None, None, None, 'no_anon'
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise NoGameAccessException('User has no access to game {}'.format(game))
    return team, user, anon_key, None


def _reveal_raddle_answer(request, task, game, team, user, anon_key, parsed, word_index, current_mode):
    """Тир 2 (💡💡): открыть ответ, послав верную посылку, чтобы слово зачлось."""
    from games.views.attempt_views import check_attempt

    n = parsed['n_words']
    with transaction.atomic():
        ChainTaskState.objects.get_or_create(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            defaults={'state': None},
        )
        row = ChainTaskState.objects.select_for_update().get(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
        )
        state = load_raddle_state(row.state, n)
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
                create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game)
            except DuplicateAttemptException:
                pass

        # Фиксируем тир 2 в state, чтобы посылка ответа зачлась с нулевым кредитом.
        state = apply_assist_tier(state, word_index, 2)
        row.state = json.dumps(state, ensure_ascii=False)
        row.save(update_fields=['state', 'updated_at'])

    word = parsed['words'][word_index]
    attempt = Attempt(text=json.dumps({'word_index': word_index, 'word': word}, ensure_ascii=False))
    attempt.team = team
    attempt.user = user
    attempt.anon_key = anon_key
    attempt.task = task
    attempt.time = timezone.now()
    attempt.game = game
    try:
        check_attempt(attempt)
    except DuplicateAttemptException:
        pass

    result = {'status': 'ok', 'task_id': task.id}
    update_html = update_task_html(
        request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
    )
    track_task_change(
        task, team, current_mode, update_html=update_html, request=request, game=game,
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
            parsed, word_index, current_mode,
        )

    with transaction.atomic():
        ChainTaskState.objects.get_or_create(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
            defaults={'state': None},
        )
        chain_row = ChainTaskState.objects.select_for_update().get(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=current_mode,
        )
        state = load_raddle_state(chain_row.state, n)
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
                create_hint_attempt(hint, team=team, user=user, anon_key=anon_key, game=game)
            except DuplicateAttemptException:
                return {'status': 'duplicate'}

        state = apply_assist_tier(state, word_index, tier)
        chain_row.state = json.dumps(state, ensure_ascii=False)
        chain_row.save(update_fields=['state', 'updated_at'])

    result = {'status': 'ok', 'task_id': task.id}
    update_html = update_task_html(
        request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
    )
    track_task_change(
        task, team, current_mode, update_html=update_html, request=request, game=game,
    )
    result.update(update_html)
    return result


@require_http_methods(['POST'])
def send_raddle_assist(request, task_id):
    try:
        response = process_send_raddle_assist(request, task_id)
    except NoGameAccessException:
        response = {'status': 'no_access'}
    return JsonResponse(response)
