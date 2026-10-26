"""Публичная страница создания алфавиток (/create_alphabetty/)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from games.club.offers.alphabetty import (
    AlphabettyOfferError,
    create_offer,
    list_user_offers,
    profile_ready_for_offers,
    request_revision,
    send_offer,
    serialize_offer,
    update_offer_content,
)
from games.models import AlphabettyOffer
from games.offer_profiles import update_offer_profile_names
from games.telegram_linking import offer_profile_error
from games.site.views.pages.util import has_profile, parse_json_object_body


def _offer_for_user(request, offer_id):
    return (
        AlphabettyOffer.objects.select_related('task_group', 'accepted_link')
        .filter(pk=offer_id, user=request.user)
        .first()
    )


def _profile_context(profile, *, ready, missing, offers, profile_error=None):
    return {
        'page_title': 'Создать свою Алфавитку',
        'profile_ready': ready,
        'profile_missing': missing,
        'profile': profile,
        'offers_json': [offer.to_dict() for offer in offers],
        'show_sections_nav': True,
        'profile_error': profile_error,
        'back_url': '/alphabetty/',
        'back_label': 'К Алфавиткам',
    }


@login_required
@require_http_methods(['GET', 'POST'])
def offer_alphabetty_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_alphabetty/')
    profile = request.user.profile
    ready, missing = profile_ready_for_offers(profile)

    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        ready_after, missing_after = update_offer_profile_names(
            profile, request.POST.get('first_name'), request.POST.get('last_name'),
        )
        if not ready_after:
            return render(request, 'new/create_alphabetty.html', _profile_context(
                profile,
                ready=False,
                missing=missing_after,
                offers=[],
                profile_error=offer_profile_error(missing_after),
            ))
        return redirect('new_create_alphabetty')

    offers = list_user_offers(request.user) if ready else []
    return render(request, 'new/create_alphabetty.html', _profile_context(
        profile, ready=ready, missing=missing, offers=offers,
    ))


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
    body = parse_json_object_body(request)
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
        return JsonResponse({'ok': False, 'error': 'Возврат доступен только для принятой Алфавитки'}, status=400)
    try:
        offer = request_revision(offer, admin_note='')
    except AlphabettyOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})
