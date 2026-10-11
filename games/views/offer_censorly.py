"""Player page for creating Wikipedia Censorly games and suggesting them daily."""

from __future__ import annotations

import json
import time

from django.contrib.auth.decorators import login_required
from django.core.cache import caches
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from games.censorly.random_game import create_from_title
from games.censorly.wiki import WikiFetchError, suggest_article_titles
from games.censorly_offer import create_offer, list_user_offers, serialize_offer
from games.ladder_offer import profile_ready_for_offers
from games.telegram_linking import offer_profile_error
from games.views.util import has_profile

SUGGEST_LIMIT = 60
SUGGEST_WINDOW_SECONDS = 60
suggestion_cache = caches['censorly_api']


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


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


@login_required
def offer_censorly_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_censorly/')
    profile = request.user.profile
    ready, missing = profile_ready_for_offers(profile)
    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        profile.first_name = (request.POST.get('first_name') or '').strip()
        profile.last_name = (request.POST.get('last_name') or '').strip()
        profile.save(update_fields=['first_name', 'last_name'])
        ready, missing = profile_ready_for_offers(profile)
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
    rate_key = f'censorly_wiki_suggest_rate_v1:{request.user.pk}:{int(time.time()) // SUGGEST_WINDOW_SECONDS}'
    if not suggestion_cache.add(rate_key, 1, timeout=SUGGEST_WINDOW_SECONDS):
        try:
            count = suggestion_cache.incr(rate_key)
        except ValueError:
            suggestion_cache.add(rate_key, 1, timeout=SUGGEST_WINDOW_SECONDS)
            count = 1
        if count > SUGGEST_LIMIT:
            return JsonResponse(
                {'ok': False, 'error': 'Слишком много запросов. Подождите минуту.'},
                status=429,
                headers={'Retry-After': str(SUGGEST_WINDOW_SECONDS)},
            )
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
    body = _json_body(request)
    if not isinstance(body, dict):
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    title = str(body.get('title') or '').strip()
    if not title:
        return JsonResponse({'ok': False, 'error': 'Выберите статью Wikipedia'}, status=400)
    if len(title) > 255:
        return JsonResponse({'ok': False, 'error': 'Название слишком длинное'}, status=400)
    try:
        party = create_from_title(title)
    except WikiFetchError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        offer = create_offer(request.user, party, comment=str(body.get('comment') or ''))
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
