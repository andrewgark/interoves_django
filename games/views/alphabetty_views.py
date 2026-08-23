"""Алфавитка: hub, play page, guess API, prefix expand."""

from __future__ import annotations

import json
import uuid

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from games.alphabetty.core import build_prefix_level, normalize_word
from games.alphabetty_offer import can_access_offer_hash, get_offer_by_share_hash
from games.alphabetty.play import (
    ALPHABETTY_HINT_PENALTY,
    apply_guess,
    apply_hint,
    get_play_state,
    get_task_for_number,
    hint_count,
    hub_progress_for_actor,
    load_state,
    ru_hint_word,
)
from games.analytics import (
    PlayerCompletedGame,
    is_task_completion_state,
    register_completed_game,
    register_started_game,
)
from games.daily_transitions import next_daily_content_transition_for_game
from games.alphabetty.suggestions import suggest_word
from games.alphabetty_daily import (
    ALPHABETTY_GAME_ID,
    alphabetty_publish_at,
    current_alphabetty_number,
    filter_published_alphabetty_links,
    get_alphabetty_hub_context,
    is_alphabetty_number_published,
    visible_alphabetty_links,
)
from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    GameTaskGroup,
    Like,
    Task,
)
from games.section_hub import section_format_credit_context
from games.section_paths import section_hub_path, section_play_path, section_results_path
from games.task_titles import task_group_page_title
from games.views.new_ui import (
    NEW_UI_SECTIONS_PROJECT,
    _anon_key_from_request,
    _neighbors_by_pk,
    _task_group_page_nav_context,
)
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


def _alphabetty_hints_taken(*, game, task, user, anon_key) -> int:
    qs = ChainTaskState.objects.filter(task=task, game=game, game_mode='general')
    if user is not None:
        qs = qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        qs = qs.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    else:
        return 0
    row = qs.first()
    if row is None:
        return 0
    return hint_count(load_state(row.state))


def _alphabetty_meta_context(request, *, game, task, user, anon_key):
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    ai = Attempt.manager.get_attempts_info(
        team=None,
        task=task,
        mode=mode,
        user=user,
        anon_key=anon_key,
        game=game,
    )
    hints_n = _alphabetty_hints_taken(game=game, task=task, user=user, anon_key=anon_key)
    return {
        'game': game,
        'task': task,
        'ai': ai,
        'mode': mode,
        'base_max': task.get_points(),
        'wall_max_title': '',
        'task_ui': {
            'show_attempts': False,
            'show_answer': False,
            'alphabetty_hints_label': (
                f'{hints_n} {ru_hint_word(hints_n)}' if hints_n > 0 else ''
            ),
        },
        'is_daily_single_task': True,
        'has_profile_user': has_profile(request.user),
        'user': request.user,
        'likes_meta_by_task_id': {
            task.id: {
                'likes': Like.manager.get_total_likes(task),
                'dislikes': Like.manager.get_total_dislikes(task),
                'liked': Like.manager.actor_has_like(
                    task, team=None, user=user, anon_key=anon_key,
                ),
                'disliked': Like.manager.actor_has_dislike(
                    task, team=None, user=user, anon_key=anon_key,
                ),
            },
        },
        'alphabetty_hints': hints_n,
        'alphabetty_hints_label': (
            f'{hints_n} {ru_hint_word(hints_n)}' if hints_n > 0 else ''
        ),
    }


def _alphabetty_meta_bar_html(request, *, game, task, user, anon_key) -> str:
    return render_to_string(
        'new/task-content/task-meta-bar.html',
        _alphabetty_meta_context(
            request, game=game, task=task, user=user, anon_key=anon_key,
        ),
        request=request,
    )


def _with_meta_bar(payload: dict, request, *, game, task, user, anon_key) -> dict:
    out = dict(payload)
    out['meta_bar_html'] = _alphabetty_meta_bar_html(
        request, game=game, task=task, user=user, anon_key=anon_key,
    )
    return out


def _resolve_round_task_group(game, *, number=None, task_group_id=None):
    qs = GameTaskGroup.objects.filter(game=game)
    if task_group_id is not None:
        return qs.filter(pk=task_group_id).select_related('task_group').first()
    if number is not None:
        return qs.filter(number=str(number)).select_related('task_group').first()
    return None


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
            'name': f'Алфавитка №{n}',
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
        'section_results_url': section_results_path(ALPHABETTY_GAME_ID),
        'can_see_results': game.has_access('see_results', team=team),
        'live_next_transition_at': next_daily_content_transition_for_game(game),
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
    cta_number = hub.get('alphabetty_cta_number')
    if not cta_number:
        return redirect('new_alphabetty_hub')
    return redirect(section_play_path(ALPHABETTY_GAME_ID, cta_number))


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
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None or game is None or task is None:
        raise Http404()
    offer = load_meta.get('offer') if load_meta else None
    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    link = load_meta.get('accepted_link') if load_meta else None
    if offer is None:
        try:
            n = int(play_number)
        except (TypeError, ValueError):
            raise Http404()
    else:
        n = play_number

    user, anon_key = _resolve_actor(request)
    # Не генерируем anon на сервере: иначе перетирается localStorage из base.html
    # и теряется прогресс. Клиент подтянет state через /state/ со своим ключом.
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=_share_host(request),
        play_path=play_path,
    )
    pub_at = alphabetty_publish_at(game, n) if offer is None else None
    daily_publish_date = pub_at.date() if pub_at is not None else None
    # Соседи только среди уже вышедших алфавиток (как у лесенок).
    prev_tg = None
    next_tg = None
    if offer is None and link is not None:
        visible_links = list(
            visible_alphabetty_links(
                GameTaskGroup.objects.filter(game=game),
                game,
            )
        )
        prev_tg, next_tg = _neighbors_by_pk(visible_links, link)
    team = None
    if user is not None and has_profile(user):
        team = user.profile.team_on
    meta_ctx = _alphabetty_meta_context(
        request, game=game, task=task, user=user, anon_key=anon_key,
    )
    return render(request, 'new/alphabetty_play.html', {
        'game': game,
        'number': play_number,
        'link': link,
        'task': task,
        'page_title': (
            task_group_page_title(game, link)
            if offer is None and link is not None
            else f'Алфавитка #{play_number}'
        ),
        'daily_publish_date': daily_publish_date,
        'live_next_transition_at': (
            next_daily_content_transition_for_game(game) if offer is None else None
        ),
        'section_results_url': section_results_path(ALPHABETTY_GAME_ID),
        'task_results_url': f'{play_path}results/',
        'can_see_results': offer is None and game.has_access('see_results', team=team),
        'daily_footer_enabled': True,
        'daily_game_label': 'Алфавитка',
        'daily_results_url': f'{play_path}results/',
        'daily_results_allowed': offer is None and game.has_access('see_results', team=team),
        'daily_results_label': 'Результаты',
        **section_format_credit_context(ALPHABETTY_GAME_ID),
        'daily_pager_aria_label': 'Переход между алфавитками',
        'show_sections_nav': True,
        'back_url': '/create_alphabetty/' if offer is not None else section_hub_path(ALPHABETTY_GAME_ID),
        'back_label': 'К списку',
        'bootstrap': state,
        'guess_url': f'{play_path}guess/',
        'state_url': f'{play_path}state/',
        'prefix_url': f'{play_path}prefix/',
        'hint_url': f'{play_path}hint/',
        'alphabetty_hint_penalty': ALPHABETTY_HINT_PENALTY,
        'suggest_url': f'{play_path}suggest/',
        'anon_key': anon_key if user is None else '',
        'is_authenticated': bool(user),
        'prev_task_group_url': (
            section_play_path(ALPHABETTY_GAME_ID, prev_tg.number) if prev_tg else None
        ),
        'next_task_group_url': (
            section_play_path(ALPHABETTY_GAME_ID, next_tg.number) if next_tg else None
        ),
        **meta_ctx,
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
    })

def _preview_denied(request, game):
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    if not game.has_access('see_game_preview', team=team):
        return JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    return None


def _load_visible_task(request, number):
    game = _get_game()
    if not game:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    offer = get_offer_by_share_hash(str(number))
    if offer is not None:
        if not can_access_offer_hash(offer, request.user):
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
        if task is None:
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        meta = {
            'offer': offer,
            'play_number': offer.share_hash,
            'play_path': offer.play_url(),
            'accepted_link': offer.accepted_link,
        }
        return game, task, meta, None
    denied = _preview_denied(request, game)
    if denied is not None:
        return None, None, None, denied
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None, None, None, JsonResponse({'status': 'error', 'error': 'bad number'}, status=400)
    if not is_alphabetty_number_published(game, n):
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not published'}, status=404)
    try:
        link, task = get_task_for_number(game, n)
    except LookupError:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    meta = {
        'offer': None,
        'play_number': n,
        'play_path': section_play_path(ALPHABETTY_GAME_ID, n),
        'accepted_link': link,
    }
    return game, task, meta, None


@require_http_methods(['GET'])
def alphabetty_state(request, number):
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err
    user, anon_key = _resolve_actor(request)
    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=_share_host(request),
        play_path=play_path,
    )
    payload = _with_meta_bar(
        {'status': 'ok', **state},
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
    )
    response = JsonResponse(payload)
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
    game, task, load_meta, err = _load_visible_task(request, number)
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

    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    result = apply_guess(
        game=game,
        task=task,
        word=word,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=_share_host(request),
        play_path=play_path,
    )
    analytics_events = []
    if result.get('status') in ('earlier', 'later', 'correct'):
        analytics_events.extend(register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        ))
    if is_task_completion_state(task, json.dumps({
        'guesses': result.get('guesses') or [],
        'won': result.get('won'),
        'hint_prefix': result.get('hint_prefix') or '',
        'hints_taken': result.get('hints') or 0,
    })):
        analytics_events.extend(register_completed_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
            result=PlayerCompletedGame.RESULT_SOLVED,
        ))
    if analytics_events:
        result['analytics_events'] = analytics_events
    result = _with_meta_bar(
        result, request, game=game, task=task, user=user, anon_key=anon_key,
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
    game, _task, _load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)

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


@require_POST
def alphabetty_hint(request, number):
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}

    user, anon_key = _resolve_actor(request, body=body)
    if user is None and not anon_key:
        anon_key = uuid.uuid4().hex

    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    result = apply_hint(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=_share_host(request),
        play_path=play_path,
    )
    if result.get('status') == 'ok':
        analytics_events = register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        )
        if analytics_events:
            result['analytics_events'] = analytics_events
    result = _with_meta_bar(
        result, request, game=game, task=task, user=user, anon_key=anon_key,
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


@require_POST
def alphabetty_suggest(request, number):
    """Предложить слово в словарь (pending → модерация в админке)."""
    game, task, _load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}
    word = body.get('word') or request.POST.get('word') or ''
    user, anon_key = _resolve_actor(request, body=body)
    if user is None and not anon_key:
        anon_key = uuid.uuid4().hex

    result = suggest_word(word, user=user, anon_key=anon_key)
    response = JsonResponse(result)
    if user is None and anon_key:
        response.set_cookie(
            'interoves_anon',
            anon_key,
            max_age=60 * 60 * 24 * 365,
            samesite='Lax',
        )
    return response
