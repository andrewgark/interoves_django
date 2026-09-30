"""Цензурки: staff/support play pages and guess API (phase 1)."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from games.censorly import CENSORLY_GAME_ID
from games.censorly.play import apply_guess, get_play_state, puzzle_from_task
from games.models import Game, RandomCensorlyGame, Task
from games.support.access import user_has_support_access
from games.views.new_ui import NEW_UI_SECTIONS_PROJECT


def _may_play_censorly(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False):
        return True
    return user_has_support_access(user)


def _get_game() -> Game | None:
    return Game.objects.filter(
        id=CENSORLY_GAME_ID,
        project_id=NEW_UI_SECTIONS_PROJECT,
    ).first()


def _load_random(share_hash: str):
    row = (
        RandomCensorlyGame.objects.filter(share_hash=str(share_hash))
        .select_related('task_group')
        .first()
    )
    if row is None:
        raise LookupError('not_found')
    task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    if task is None:
        raise LookupError('task_not_found')
    return row, task


@login_required
def censorly_hub_page(request):
    if not _may_play_censorly(request.user):
        raise Http404()
    game = _get_game()
    if game is None:
        raise Http404()
    rows = list(
        RandomCensorlyGame.objects.order_by('-created_at')[:40]
    )
    return render(request, 'new/censorly_hub.html', {
        'page_title': 'Цензурки (тест)',
        'game': game,
        'rows': rows,
        'support_url': '/support/censorly/',
    })


@login_required
def censorly_play_page(request, share_hash):
    if not _may_play_censorly(request.user):
        raise Http404()
    game = _get_game()
    if game is None:
        raise Http404()
    try:
        row, task = _load_random(share_hash)
    except LookupError:
        raise Http404()
    payload = puzzle_from_task(task)
    if payload is None:
        raise Http404()
    state = get_play_state(game=game, task=task, user=request.user)
    bootstrap = {
        'share_hash': row.share_hash,
        'guess_url': f'/censorly/r/{row.share_hash}/guess/',
        'state_url': f'/censorly/r/{row.share_hash}/state/',
        'state': state,
    }
    return render(request, 'new/censorly_play.html', {
        'page_title': f'Цензурка · {row.wiki_title}' if state.get('won') else 'Цензурка',
        'game': game,
        'task': task,
        'row': row,
        'bootstrap': bootstrap,
        'back_url': '/censorly/',
        'back_label': 'К списку',
    })


@login_required
@require_http_methods(['GET'])
def censorly_state(request, share_hash):
    if not _may_play_censorly(request.user):
        return JsonResponse({'status': 'error', 'error': 'Forbidden'}, status=403)
    game = _get_game()
    if game is None:
        return JsonResponse({'status': 'error', 'error': 'Not found'}, status=404)
    try:
        _row, task = _load_random(share_hash)
    except LookupError:
        return JsonResponse({'status': 'error', 'error': 'Not found'}, status=404)
    return JsonResponse(get_play_state(game=game, task=task, user=request.user))


@login_required
@require_POST
def censorly_guess(request, share_hash):
    if not _may_play_censorly(request.user):
        return JsonResponse({'status': 'error', 'error': 'Forbidden'}, status=403)
    game = _get_game()
    if game is None:
        return JsonResponse({'status': 'error', 'error': 'Not found'}, status=404)
    try:
        _row, task = _load_random(share_hash)
    except LookupError:
        return JsonResponse({'status': 'error', 'error': 'Not found'}, status=404)

    word = ''
    content_type = (request.content_type or '').lower()
    if 'application/json' in content_type:
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
        except (TypeError, ValueError, UnicodeDecodeError):
            body = {}
        word = (body.get('word') or body.get('guess') or '').strip()
    else:
        word = (request.POST.get('word') or request.POST.get('guess') or '').strip()

    result = apply_guess(game=game, task=task, word=word, user=request.user)
    status_code = 200
    if result.get('status') == 'error':
        status_code = 400
    return JsonResponse(result, status=status_code)
