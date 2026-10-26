"""Player page for creating Wikipedia Censorly games and suggesting them daily."""

from __future__ import annotations

import time

from django.contrib.auth.decorators import login_required
from django.core.cache import caches
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from games.puzzles.censorly.random_game import create_from_title
from games.puzzles.censorly.article.wiki import WikiFetchError, suggest_article_titles
from games.club.offers.censorly import create_offer, list_user_offers, serialize_offer
from games.club.offers.profiles import profile_ready_for_offers, update_offer_profile_names
from games.club.link import offer_profile_error
from games.identity import has_profile
from games.site.http.request_util import parse_json_object_body

SUGGEST_LIMIT = 60
SUGGEST_WINDOW_SECONDS = 60
CREATE_LIMIT = 30
CREATE_WINDOW_SECONDS = 60 * 60
suggestion_cache = caches['censorly_api']


def _page_context(profile, *, ready, missing, offers, error=None):
    return {
        'page_title': 'Создать Цензурку',
        'profile_ready': ready,
        'profile_missing': missing,
        'profile': profile,
        'offers_json': [offer.to_dict() for offer in offers],
        'profile_error': error,
        'show_sections_nav': True,
        'back_url': '/censorly/',
        'back_label': 'К Цензуркам',
    }


def _rate_limit_response(user_id, action, *, limit, window_seconds, message):
    now = int(time.time())
    bucket = now // window_seconds
    cache_key = f'censorly_rate_v1:{action}:{user_id}:{bucket}'
    if suggestion_cache.add(cache_key, 1, timeout=window_seconds):
        return None
    try:
        count = suggestion_cache.incr(cache_key)
    except ValueError:
        suggestion_cache.add(cache_key, 1, timeout=window_seconds)
        count = 1
    if count <= limit:
        return None
    return JsonResponse(
        {'ok': False, 'error': message},
        status=429,
        headers={'Retry-After': str(window_seconds - now % window_seconds)},
    )


@login_required
@require_http_methods(['GET', 'POST'])
def offer_censorly_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_censorly/')
    profile = request.user.profile
    ready, missing = profile_ready_for_offers(profile)
    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        ready, missing = update_offer_profile_names(
            profile, request.POST.get('first_name'), request.POST.get('last_name'),
        )
        if ready:
            return redirect('ui_create_censorly')
        return render(request, 'new/create_censorly.html', _page_context(
            profile, ready=False, missing=missing, offers=[], error=offer_profile_error(missing),
        ))
    offers = list_user_offers(request.user) if ready else []
    return render(request, 'new/create_censorly.html', _page_context(
        profile, ready=ready, missing=missing, offers=offers,
    ))


@login_required
@require_GET
def offer_censorly_suggest(request):
    query = (request.GET.get('q') or '').strip()
    if len(query) < 2:
        return JsonResponse({'ok': True, 'titles': []})
    limited = _rate_limit_response(
        request.user.pk, 'suggest', limit=SUGGEST_LIMIT,
        window_seconds=SUGGEST_WINDOW_SECONDS,
        message='Слишком много запросов. Подождите минуту.',
    )
    if limited is not None:
        return limited
    try:
        titles = suggest_article_titles(query)
    except WikiFetchError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
    return JsonResponse({'ok': True, 'titles': titles})


@login_required
@require_POST
def offer_censorly_create(request):
    if not has_profile(request.user):
        return JsonResponse({'ok': False, 'error': 'Нужен профиль'}, status=403)
    ready, _missing = profile_ready_for_offers(request.user.profile)
    if not ready:
        return JsonResponse({'ok': False, 'error': 'Заполните профиль и привяжите Telegram'}, status=403)
    body = parse_json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    title = str(body.get('title') or '').strip()
    if not title:
        return JsonResponse({'ok': False, 'error': 'Выберите статью Wikipedia'}, status=400)
    if len(title) > 255:
        return JsonResponse({'ok': False, 'error': 'Название слишком длинное'}, status=400)
    limited = _rate_limit_response(
        request.user.pk, 'create', limit=CREATE_LIMIT,
        window_seconds=CREATE_WINDOW_SECONDS,
        message='Можно создать не больше 30 Цензурок в час. Попробуйте позже.',
    )
    if limited is not None:
        return limited
    try:
        party = create_from_title(title)
    except WikiFetchError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        offer = create_offer(request.user, party, comment=str(body.get('comment') or ''))
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
