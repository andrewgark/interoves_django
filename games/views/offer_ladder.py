"""Публичная страница предложений лесенок (/offer_ladder/)."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from games.ladder_offer import (
    LadderOfferError,
    create_offer,
    list_user_offers,
    normalize_telegram_handle,
    profile_display_name,
    profile_ready_for_offers,
    reset_raddle_progress,
    send_offer,
    serialize_offer,
    update_offer_content,
)
from games.models import LadderOffer
from games.views.util import has_profile


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _offer_for_user(request, offer_id):
    return (
        LadderOffer.objects.select_related('task_group', 'accepted_link')
        .filter(pk=offer_id, user=request.user)
        .first()
    )


@login_required
@require_http_methods(['GET', 'POST'])
def offer_ladder_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/offer_ladder/')
    profile = request.user.profile
    ready, missing = profile_ready_for_offers(profile)

    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        profile.first_name = (request.POST.get('first_name') or '').strip()
        profile.last_name = (request.POST.get('last_name') or '').strip()
        profile.telegram_handle = normalize_telegram_handle(
            request.POST.get('telegram_handle') or ''
        )
        profile.save(update_fields=['first_name', 'last_name', 'telegram_handle'])
        ready_after, missing_after = profile_ready_for_offers(profile)
        if not ready_after:
            return render(request, 'ui/offer_ladder.html', {
                'page_title': 'Предложить лесенку',
                'profile_ready': False,
                'profile_missing': missing_after,
                'profile': profile,
                'offers': [],
                'offers_json': [],
                'show_sections_nav': True,
                'profile_error': (
                    'Проверьте Telegram-хэндл (5–32 символа: латиница, цифры, _).'
                    if 'telegram_handle_invalid' in missing_after
                    else 'Заполните имя, фамилию и Telegram.'
                ),
            })
        return redirect('new_offer_ladder')

    offers = list_user_offers(request.user) if ready else []
    return render(request, 'ui/offer_ladder.html', {
        'page_title': 'Предложить лесенку',
        'profile_ready': ready,
        'profile_missing': missing,
        'profile': profile,
        'offers': offers,
        'offers_json': [o.to_dict() for o in offers],
        'show_sections_nav': True,
    })


@login_required
@require_POST
def offer_ladder_create(request):
    if not has_profile(request.user):
        return JsonResponse({'ok': False, 'error': 'Нужен профиль'}, status=403)
    try:
        offer = create_offer(request.user)
        row = serialize_offer(offer)
    except LadderOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': row.to_dict()})


@login_required
@require_http_methods(['GET', 'POST'])
def offer_ladder_detail(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    words = body.get('words')
    hints = body.get('hints')
    if not isinstance(words, list) or not isinstance(hints, list):
        return JsonResponse({'ok': False, 'error': 'Нужны words и hints'}, status=400)
    try:
        offer = update_offer_content(
            offer,
            words=words,
            hints=hints,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or profile_display_name(request.user.profile)),
            comment=str(body.get('comment') or ''),
            mixed_script=bool(body.get('mixed_script')),
            reset_actor_user=request.user,
        )
    except LadderOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_ladder_send(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = send_offer(offer)
    except LadderOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_ladder_reset(request, offer_id):
    """Сброс решения автора (или staff) на своей лесенке."""
    offer = (
        LadderOffer.objects.select_related('task_group')
        .filter(pk=offer_id)
        .first()
    )
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    is_owner = offer.user_id == request.user.id
    is_staff = bool(getattr(request.user, 'is_staff', False))
    if not is_owner and not is_staff:
        return JsonResponse({'ok': False, 'error': 'Нет доступа'}, status=403)
    task = offer.task_group.tasks.filter(number='1').first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Задание не найдено'}, status=404)
    try:
        n = reset_raddle_progress(task=task, user=request.user)
    except LadderOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'deleted_attempts': n})
