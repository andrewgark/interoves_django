"""Цензурка as a task of an ordinary game (a Десяточка), not a daily edition.

A task card in the game links to a full page per task; the board is the daily
one (site/partials/censorly_board.html, static/js/censorly_play.js). What
differs from the daily Цензурка:

- the actor is the team in team mode (one shared board), else the player;
- the board's game mode follows the game: 'tournament' while it runs;
- the task's or its group's max_attempts caps guesses (hints are free);
- no share card, solve timer, statistics, replay or random game.
"""

from __future__ import annotations

import json

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from games.identity import ActorRefusal, has_profile, has_team, resolve_gameplay_actor
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Task
from games.puzzles.censorly import CensorlyMoveRefusal, CensorlyPuzzle
from games.puzzles.censorly.play import get_play_state, hint_count, hint_penalty_for, load_state, ru_hint_word
from games.site.http.auth_observability import record_gameplay_attempt_created
from games.site.http.exceptions import NoGameAccessException
from games.site.pages.game.access import _maybe_registration_or_announce_response
from games.site.pages.shared import NEW_UI_SECTIONS_PROJECT, _anon_key_from_request, _get_play_mode
from games.site.session.gameplay_context import (
    context_error_response,
    issue_gameplay_context,
    validate_gameplay_context,
)
from games.site.session.play_mode import effective_play_mode, personal_play_mode_locked
from games.site.views.game.context import gameplay_play_mode
from games.task_group.likes_meta import likes_meta_by_task_id
from games.task_group.titles import task_display_name

CENSORLY_TASK_TYPE = 'censorly'


def censorly_task_path(game_id, task_id) -> str:
    return f'/games/{game_id}/censorly/{task_id}/'


def _load(game_id, task_id):
    """(game, placement, task) of a Цензурка in an ordinary game, else 404."""
    game = get_object_or_404(Game, id=game_id)
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        # Daily Цензурки have their own pages.
        raise Http404()
    task = (
        Task.objects.visible()
        .select_related('task_group')
        .filter(pk=task_id, task_type=CENSORLY_TASK_TYPE)
        .first()
    )
    if task is None or not task.task_group_id:
        raise Http404()
    placement = (
        GameTaskGroup.objects.select_related('task_group')
        .filter(game=game, task_group_id=task.task_group_id)
        .first()
    )
    if placement is None:
        raise Http404()
    return game, placement, task


def _current_mode(game) -> str:
    return game.get_current_mode(Attempt(time=timezone.now()))


def _page_actor(request, game):
    """(team, user, anon_key) the page plays as, or a response to return.

    The same rules as the game's task-group page (games.site.pages.play.page).
    """
    play_mode = effective_play_mode(
        _get_play_mode(request, game.project_id), game, user=request.user,
    )
    anon_key = None
    if not request.user.is_authenticated:
        if personal_play_mode_locked(game, user=request.user):
            from urllib.parse import quote
            return redirect('/accounts/login/?next={}'.format(quote(request.get_full_path())))
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
    elif play_mode == 'personal' and not has_profile(request.user):
        raise Http404()
    if play_mode == 'team':
        if not has_team(request.user):
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
        return team, None, None
    if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
        raise Http404()
    if not game.is_playable:
        raise Http404()
    user = request.user if request.user.is_authenticated else None
    return None, user, anon_key


def _move_actor(request, game, task):
    """(team, user, anon_key) of a move or state request, or a JSON refusal."""
    try:
        resolved = resolve_gameplay_actor(request, game, gameplay_play_mode(request, game))
    except NoGameAccessException:
        return JsonResponse({'status': 'error', 'error': 'Нет доступа к игре'}, status=403)
    if isinstance(resolved, ActorRefusal):
        return JsonResponse({'status': 'error', 'error': resolved.status}, status=403)
    return resolved.team, resolved.user, resolved.anon_key


def _hints_taken(*, game, task, team, user, anon_key, mode) -> int:
    qs = ChainTaskState.objects.filter(
        task=task, game=game, game_mode=mode, replay_slot__isnull=True,
    )
    if team is not None:
        qs = qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        qs = qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        qs = qs.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    else:
        return 0
    row = qs.first()
    return hint_count(load_state(row.state)) if row is not None else 0


def _meta_context(request, *, game, task, team, user, anon_key, mode):
    """Context of task-meta-bar.html: points so far of the task's maximum, hints."""
    ai = Attempt.manager.get_attempts_info(
        team=team, task=task, mode=mode, user=user, anon_key=anon_key, game=game,
    )
    hints_n = _hints_taken(
        game=game, task=task, team=team, user=user, anon_key=anon_key, mode=mode,
    )
    label = f'{hints_n} {ru_hint_word(hints_n)}' if hints_n else ''
    return {
        'game': game,
        'task': task,
        'ai': ai,
        'mode': mode,
        'base_max': task.get_results_max_points(),
        'wall_max_title': '',
        'task_ui': {'show_attempts': False, 'show_answer': False, 'hints_label': label},
        'is_daily_single_task': False,
        'has_profile_user': has_profile(request.user),
        'user': request.user,
        'likes_meta_by_task_id': likes_meta_by_task_id(
            [task], team=team, user=user, anon_key=anon_key,
        ),
        'hints_taken': hints_n,
        'hints_label': label,
    }


def _with_meta_bar(payload, request, **actor):
    out = dict(payload)
    out['meta_bar_html'] = render_to_string(
        'site/task-content/task-meta-bar.html',
        _meta_context(request, **actor),
        request=request,
    )
    return out


def _json(payload, *, status=200):
    # The masked article is large: no \\u escapes, no padding.
    return JsonResponse(
        payload, status=status,
        json_dumps_params={'ensure_ascii': False, 'separators': (',', ':')},
    )


def censorly_game_task_page(request, game_id, task_id):
    game, placement, task = _load(game_id, task_id)
    gate = _maybe_registration_or_announce_response(
        request, game, back_url='/games/', show_sections_nav=True,
    )
    if gate is not None:
        return gate
    actor = _page_actor(request, game)
    if not isinstance(actor, tuple):
        return actor
    team, user, anon_key = actor
    mode = _current_mode(game)
    play_path = censorly_task_path(game.id, task.id)
    state = get_play_state(
        game=game, task=task, user=user, anon_key=anon_key, team=team, mode=mode,
        number=placement.number, play_path=play_path,
    )
    # The article title is the answer: never before a win.
    label = task_display_name(game, task, placement=placement)
    page_title = '{}: {}'.format(game.name, label)
    return render(request, 'site/censorly_game_play.html', {
        'game': game,
        'task': task,
        'placement': placement,
        'page_title': page_title,
        'page_heading': label,
        'bug_report_task_label': label,
        'back_url': f'/games/{game.id}/{placement.number}/',
        'back_label': placement.name or 'К заданиям',
        'play_mode': 'team' if team is not None else 'personal',
        'bootstrap': {
            **state,
            'guess_url': f'{play_path}guess/',
            'state_url': f'{play_path}state/',
            'hint_url': f'{play_path}hint/',
        },
        'guess_url': f'{play_path}guess/',
        'state_url': f'{play_path}state/',
        'hint_url': f'{play_path}hint/',
        'censorly_hint_penalty': hint_penalty_for(task),
        'anon_key': anon_key or '',
        'is_authenticated': bool(request.user.is_authenticated),
        'gameplay_context_token': issue_gameplay_context(
            task=task, game=game, team=team, user=user, anon_key=anon_key,
        ),
        **_meta_context(
            request, game=game, task=task, team=team, user=user, anon_key=anon_key, mode=mode,
        ),
    })


@require_http_methods(['GET'])
def censorly_game_task_state(request, game_id, task_id):
    game, placement, task = _load(game_id, task_id)
    actor = _move_actor(request, game, task)
    if not isinstance(actor, tuple):
        return actor
    team, user, anon_key = actor
    mode = _current_mode(game)
    state = get_play_state(
        game=game, task=task, user=user, anon_key=anon_key, team=team, mode=mode,
        number=placement.number, play_path=censorly_task_path(game.id, task.id),
    )
    return _json(_with_meta_bar(
        {'status': 'ok', **state}, request,
        game=game, task=task, team=team, user=user, anon_key=anon_key, mode=mode,
    ))


def _body(request) -> dict:
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Moves that change the board: the meta bar (points, hints) is rendered again.
_META_BAR_STATUSES = frozenset({'hint', 'won', 'already_won'})
# Moves that changed the board: teammates' pages and the task list sync.
_STATE_CHANGING_STATUSES = frozenset({'hit', 'miss', 'hint', 'won'})


def _move(request, game_id, task_id, apply_move):
    game, placement, task = _load(game_id, task_id)
    actor = _move_actor(request, game, task)
    if not isinstance(actor, tuple):
        return actor
    team, user, anon_key = actor
    context_error = validate_gameplay_context(
        request, task=task, game=game, team=team, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error_response(context_error)
    body = _body(request)
    mode = _current_mode(game)
    result = apply_move(
        body,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        team=team,
        mode=mode,
        number=placement.number,
        play_path=censorly_task_path(game.id, task.id),
        submitted_by_profile_id=request.user.profile.pk if has_profile(request.user) else None,
        attempt_created_callback=lambda attempt: record_gameplay_attempt_created(
            request, attempt=attempt,
            actor_kind='team' if team is not None else 'user' if user is not None else 'anon',
        ),
    )
    if isinstance(result, CensorlyMoveRefusal):
        return JsonResponse({'status': 'error', 'error': result.error}, status=400)
    # A won game page has nothing to share: the share card is a daily feature.
    for key in ('share_lines', 'share_text', 'share_card'):
        result.pop(key, None)
    if result.get('status') in _STATE_CHANGING_STATUSES:
        from games.realtime.track_compat import track_actor_task_change
        track_actor_task_change(
            task, team=team, user=user, anon_key=anon_key, game=game,
            current_mode=mode, request=request, reason='censorly.state_changed',
        )
    if result.get('status') in _META_BAR_STATUSES:
        result = _with_meta_bar(
            result, request,
            game=game, task=task, team=team, user=user, anon_key=anon_key, mode=mode,
        )
    from games.site.views.daily.censorly import _compact_play_response
    return _json(
        _compact_play_response(result),
        status=400 if result.get('status') == 'error' else 200,
    )


@require_POST
def censorly_game_task_guess(request, game_id, task_id):
    return _move(
        request, game_id, task_id,
        lambda body, **kwargs: CensorlyPuzzle.apply_posted_guess(body, request.POST, **kwargs),
    )


@require_POST
def censorly_game_task_hint(request, game_id, task_id):
    return _move(
        request, game_id, task_id,
        lambda body, **kwargs: CensorlyPuzzle.apply_posted_hint(body, **kwargs),
    )
