"""«Создать свой Криптик» (/create_cryptic/) и страница черновика по ссылке."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from games.club.link import offer_profile_error
from games.club.offers.cryptic import (
    CrypticOfferError,
    check_draft_answer,
    create_offer,
    draft_client_spec,
    draft_hints,
    get_offer_by_share_hash,
    list_user_offers,
    preview,
    send_offer,
    serialize_offer,
    update_offer_content,
)
from games.club.offers.profiles import profile_ready_for_offers, update_offer_profile_names
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID, PREVIEW_FIELDS, mask_format
from games.identity import has_profile
from games.models import CrypticOffer
from games.sections.paths import section_hub_path
from games.site.http.request_util import parse_json_object_body

_FIELDS = ('clue', 'answer', 'enumeration', 'markup', 'formula', 'comment')


def _context(profile, *, ready, offers, error=None):
    return {
        'page_title': 'Создать свой Криптик',
        'profile_ready': ready,
        'profile': profile,
        'offers_json': [offer.to_dict() for offer in offers],
        'profile_error': error,
        'show_sections_nav': True,
        'back_url': section_hub_path(CRYPTIC_GAME_ID),
        'back_label': 'Все Криптики',
    }


def _own_offer(request, offer_id):
    return (
        CrypticOffer.objects.select_related('user__profile', 'accepted_link', 'reviewed_by__profile')
        .filter(pk=offer_id, user=request.user)
        .first()
    )


def _error(message, status=400):
    return JsonResponse({'ok': False, 'error': str(message)}, status=status)


@login_required
@require_http_methods(['GET', 'POST'])
def offer_cryptic_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_cryptic/')
    profile = request.user.profile
    ready, _missing = profile_ready_for_offers(profile)
    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        ready, missing = update_offer_profile_names(
            profile, request.POST.get('first_name'), request.POST.get('last_name'),
        )
        if ready:
            return redirect('ui_create_cryptic')
        return render(request, 'site/create_cryptic.html', _context(
            profile, ready=False, offers=[], error=offer_profile_error(missing),
        ))
    offers = list_user_offers(request.user) if ready else []
    return render(request, 'site/create_cryptic.html', _context(profile, ready=ready, offers=offers))


@login_required
@require_POST
def offer_cryptic_create(request):
    if not has_profile(request.user):
        return _error('Нужен профиль', status=403)
    try:
        offer = create_offer(request.user)
    except CrypticOfferError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_http_methods(['GET', 'POST'])
def offer_cryptic_detail(request, offer_id):
    offer = _own_offer(request, offer_id)
    if offer is None:
        return _error('Не найдено', status=404)
    if request.method == 'GET':
        # Notification links point here; the list page opens the offer.
        return redirect(reverse('ui_create_cryptic') + '#offer-{}'.format(offer.pk))
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        offer = update_offer_content(offer, **{key: str(body.get(key) or '') for key in _FIELDS})
    except CrypticOfferError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_cryptic_send(request, offer_id):
    offer = _own_offer(request, offer_id)
    if offer is None:
        return _error('Не найдено', status=404)
    try:
        offer = send_offer(offer)
    except CrypticOfferError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_cryptic_preview(request):
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    return JsonResponse({'ok': True, **preview(**{
        key: str(body.get(key) or '') for key in PREVIEW_FIELDS
    })})


def draft_offer_for_segment(segment: str):
    """Ещё не принятый криптик по ссылке; принятый открывается как день расписания."""
    offer = get_offer_by_share_hash(segment)
    if offer is None or offer.accepted_link_id:
        return None
    return offer


def draft_offer_page(request, offer):
    """Черновик: подсказка и проверка без попыток. В HTML только солёный хеш ответа."""
    is_author = request.user.is_authenticated and request.user.pk == offer.user_id
    ready = bool(offer.clue and offer.answer)
    enumeration = offer.shown_enumeration
    return render(request, 'site/cryptic_offer_play.html', {
        'page_title': 'Криптик',
        'offer': offer,
        'is_author': is_author,
        'ready': ready,
        'enumeration': enumeration,
        'answer_format': mask_format(enumeration) if ready else '',
        'hints': draft_hints(offer) if ready else [],
        # Salted hashes only, as on a day's card: the answer itself stays out.
        'client_spec': draft_client_spec(offer) if ready else None,
        'author_name': serialize_offer(offer).user_name,
        'show_sections_nav': True,
        'back_url': section_hub_path(CRYPTIC_GAME_ID),
        'back_label': 'Все Криптики',
        'check_url': reverse('ui_cryptic_offer_check', args=[offer.share_hash]),
    })


@require_POST
def offer_cryptic_check(request, share_hash):
    offer = draft_offer_for_segment(share_hash)
    if offer is None or not offer.answer:
        raise Http404()
    # ``ok`` is whether the answer is right; the draft page reads only that.
    return JsonResponse(check_draft_answer(offer, request.POST.get('text') or ''))
