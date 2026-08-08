"""Публичная страница создания алфавиток (/create_alphabetty/)."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from games.alphabetty_offer import (
    AlphabettyOfferError,
    create_offer,
    list_user_offers,
    normalize_telegram_handle,
    profile_ready_for_offers,
    request_revision,
    send_offer,
    serialize_offer,
    update_offer_content,
)
from games.models import AlphabettyOffer
from games.views.util import has_profile


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _offer_for_user(request, offer_id):
    return (
        AlphabettyOffer.objects.select_related('task_group', 'accepted_link')
        .filter(pk=offer_id, user=request.user)
        .first()
    )


@login_required
@require_http_methods(['GET', 'POST'])
def offer_alphabetty_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_alphabetty/')
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
            return render(request, 'new/create_alphabetty.html', {
                'page_title': 'Создать свою алфавитку',
                'profile_ready': False,
                'profile_missing': missing_after,
                'profile': profile,
                'offers_json': [],
                'show_sections_nav': True,
                'profile_error': (
                    'Проверьте Telegram-хэндл (5–32 символа: латиница, цифры, _).'
                    if 'telegram_handle_invalid' in missing_after
                    else 'Заполните имя, фамилию и Telegram.'
                ),
                'back_url': '/alphabetty/',
                'back_label': 'К алфавиткам',
            })
        return redirect('new_create_alphabetty')

    offers = list_user_offers(request.user) if ready else []
    return render(request, 'new/create_alphabetty.html', {
        'page_title': 'Создать свою алфавитку',
        'profile_ready': ready,
        'profile_missing': missing,
        'profile': profile,
        'offers_json': [o.to_dict() for o in offers],
        'show_sections_nav': True,
        'back_url': '/alphabetty/',
        'back_label': 'К алфавиткам',
    })


@login_required
@require_POST
def offer_alphabetty_create(request):
    if not has_profile(request.user):
        return JsonResponse({'ok': False, 'error': 'Нужен профиль'}, status=403)
    try:
        offer = create_offer(request.user)
        row = serialize_offer(offer)
    except AlphabettyOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': row.to_dict()})


@login_required
@require_http_methods(['GET', 'POST'])
def offer_alphabetty_detail(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        offer = update_offer_content(
            offer,
            word=str(body.get('word') or ''),
            comment=str(body.get('comment') or ''),
        )
    except AlphabettyOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_alphabetty_send(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = send_offer(offer)
    except AlphabettyOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_alphabetty_reopen(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    if offer.status != AlphabettyOffer.STATUS_ACCEPTED:
        return JsonResponse({'ok': False, 'error': 'Возврат доступен только для принятой алфавитки'}, status=400)
    try:
        offer = request_revision(offer, admin_note='')
    except AlphabettyOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
