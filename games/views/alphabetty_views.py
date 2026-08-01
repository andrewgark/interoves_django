"""Алфавитка: hub, play page, guess API, prefix expand."""

from __future__ import annotations

import json
import uuid

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from games.alphabetty.core import build_prefix_level, normalize_word
from games.alphabetty.play import (
    apply_guess,
    get_play_state,
    get_task_for_number,
    hub_progress_for_actor,
)
from games.alphabetty_daily import (
    ALPHABETTY_GAME_ID,
    alphabetty_publish_at,
    current_alphabetty_number,
    filter_published_alphabetty_links,
    get_alphabetty_hub_context,
    is_alphabetty_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.section_paths import section_hub_path, section_play_path
from games.views.new_ui import NEW_UI_SECTIONS_PROJECT, _anon_key_from_request
from games.views.util import has_profile


def _share_host(request) -> str:
    return request.get_host() or 'interoves.com'


def _get_game():
    return Game.objects.filter(
        id=ALPHABETTY_GAME_ID,
        project_id=NEW_UI_SECTIONS_PROJECT,
    ).first()


def _published_numbers(game):
    links = GameTaskGroup.objects.filter(game=game)
    return {
        link.number
        for link in filter_published_alphabetty_links(links, game)
    }


def _resolve_actor(request, *, body=None):
    """Актор: залогиненный user, иначе anon из cookie/header/body (без генерации UUID)."""
    body = body or {}
    if request.user.is_authenticated:
        return request.user, None
    anon_key = (
        _anon_key_from_request(request)
        or request.POST.get('anon_key')
        or request.headers.get('X-Interoves-Anon')
        or body.get('anon_key')
    )
    if anon_key:
        return None, str(anon_key)
    return None, None


def alphabetty_hub_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        raise Http404()

    links = GameTaskGroup.sorted_links(
        filter_published_alphabetty_links(
            GameTaskGroup.objects.filter(game=game).select_related('task_group'),
            game,
        ),
        reverse=True,
    )
    today_number = current_alphabetty_number(game)
    link_rows = []
    for link in links:
        try:
            n = int(link.number)
        except (TypeError, ValueError):
            continue
        link_rows.append((n, link))

    tasks_by_tg = {
        t.task_group_id: t
        for t in Task.objects.filter(
            task_group_id__in=[link.task_group_id for _, link in link_rows],
            number='1',
        )
    }
    user, anon_key = _resolve_actor(request)
    progress = hub_progress_for_actor(
        game=game,
        numbers_and_tasks=[
            (n, tasks_by_tg[link.task_group_id])
            for n, link in link_rows
            if link.task_group_id in tasks_by_tg
        ],
        user=user,
        anon_key=anon_key,
    )

    rows = []
    for n, link in link_rows:
        prog = progress.get(n) or {}
        row_class = prog.get('row_class') or ''
        if not row_class and today_number is not None and n == today_number:
            row_class = 'new-task--partial'
        rows.append({
            'number': n,
            'name': link.name or f'Алфавитка #{n}',
            'play_url': section_play_path(ALPHABETTY_GAME_ID, n),
            'is_today': today_number is not None and n == today_number,
            'is_solved': bool(prog.get('is_solved')),
            'row_class': row_class,
            'progress_meta': prog.get('progress_meta') or '',
        })
    hub = get_alphabetty_hub_context(game, published_numbers=_published_numbers(game))
    return render(request, 'new/alphabetty_hub.html', {
        'game': game,
        'rows': rows,
        'hub': hub,
        'page_title': 'Алфавитка',
        'show_sections_nav': True,
        'back_url': '/',
    })


def alphabetty_today_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    hub = get_alphabetty_hub_context(game, published_numbers=_published_numbers(game))
    play_url = hub.get('alphabetty_play_url')
    if not play_url:
        return redirect('new_alphabetty_hub')
    return redirect(play_url)


def alphabetty_last_page(request):
    """Редирект на последнюю опубликованную алфавитку (как /ladder/last/)."""
    game = _get_game()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    ints = []
    for n in _published_numbers(game):
        try:
            ints.append(int(n))
        except (TypeError, ValueError):
            continue
    if not ints:
        return redirect('new_alphabetty_hub')
    return redirect(section_play_path(ALPHABETTY_GAME_ID, max(ints)))


def alphabetty_play_page(request, number):
    game = _get_game()
    if not game:
        raise Http404()
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise Http404()
    if not is_alphabetty_number_published(game, n):
        raise Http404()
    try:
        link, task = get_task_for_number(game, n)
    except LookupError:
        raise Http404()

    user, anon_key = _resolve_actor(request)
    # Не генерируем anon на сервере: иначе перетирается localStorage из base.html
    # и теряется прогресс. Клиент подтянет state через /state/ со своим ключом.
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=n,
        share_host=_share_host(request),
    )
    pub_at = alphabetty_publish_at(game, n)
    daily_publish_date = pub_at.date() if pub_at is not None else None
    return render(request, 'new/alphabetty_play.html', {
        'game': game,
        'number': n,
        'link': link,
        'task': task,
        'page_title': f'Алфавитка №{n}',
        'daily_publish_date': daily_publish_date,
        'show_sections_nav': True,
        'back_url': section_hub_path(ALPHABETTY_GAME_ID),
        'back_label': 'К списку',
        'bootstrap': state,
        'guess_url': f'{section_play_path(ALPHABETTY_GAME_ID, n)}guess/',
        'state_url': f'{section_play_path(ALPHABETTY_GAME_ID, n)}state/',
        'prefix_url': f'{section_play_path(ALPHABETTY_GAME_ID, n)}prefix/',
        'anon_key': anon_key if user is None else '',
        'is_authenticated': bool(user),
    })


def _preview_denied(request, game):
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        return JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    return None


def _load_published_task(request, number):
    game = _get_game()
    if not game:
        return None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    denied = _preview_denied(request, game)
    if denied is not None:
        return None, None, denied
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None, None, JsonResponse({'status': 'error', 'error': 'bad number'}, status=400)
    if not is_alphabetty_number_published(game, n):
        return None, None, JsonResponse({'status': 'error', 'error': 'not published'}, status=404)
    try:
        _link, task = get_task_for_number(game, n)
    except LookupError:
        return None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    return game, task, None


@require_http_methods(['GET'])
def alphabetty_state(request, number):
    game, task, err = _load_published_task(request, number)
    if err is not None:
        return err
    user, anon_key = _resolve_actor(request)
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=n,
        share_host=_share_host(request),
    )
    response = JsonResponse({'status': 'ok', **state})
    if user is None and anon_key:
        response.set_cookie(
            'interoves_anon',
            anon_key,
            max_age=60 * 60 * 24 * 365,
            samesite='Lax',
        )
    return response


@require_POST
def alphabetty_guess(request, number):
    game, task, err = _load_published_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}
    word = body.get('word') or request.POST.get('word') or ''

    user, anon_key = _resolve_actor(request, body=body)
    if user is None and not anon_key:
        # Последний шанс — клиент обязан прислать anon; иначе сгенерируем стабильный
        # только для этого ответа (и проставим cookie), не трогая чужой localStorage на GET.
        anon_key = uuid.uuid4().hex

    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    result = apply_guess(
        game=game,
        task=task,
        word=word,
        user=user,
        anon_key=anon_key,
        number=n,
        share_host=_share_host(request),
    )
    response = JsonResponse(result)
    if user is None and anon_key:
        response.set_cookie(
            'interoves_anon',
            anon_key,
            max_age=60 * 60 * 24 * 365,
            samesite='Lax',
        )
    return response


@require_http_methods(['GET', 'POST'])
def alphabetty_prefix(request, number):
    """Раскрыть уровень префиксов между текущими границами (или переданными lo/hi)."""
    game = _get_game()
    if not game:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
    denied = _preview_denied(request, game)
    if denied is not None:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
    try:
        n = int(number)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'bad number'}, status=400)
    if not is_alphabetty_number_published(game, n):
        return JsonResponse({'ok': False, 'error': 'not published'}, status=404)

    if request.method == 'POST':
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, TypeError):
            body = {}
    else:
        body = request.GET

    lo = normalize_word(body.get('lo') or '') or None
    hi = normalize_word(body.get('hi') or '') or None
    expand = normalize_word(body.get('prefix') or '')
    rows = build_prefix_level(lo, hi, expand_prefix=expand)
    return JsonResponse({'ok': True, 'rows': rows})
