"""Публичная страница создания Салатиков (/create_salad/)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from games.club.link import offer_profile_error
from games.club.offers.profiles import update_offer_profile_names
from games.club.offers.salad import (
    WordSaladOfferError,
    convert_accepted_idea,
    create_offer,
    list_user_offers,
    profile_ready_for_offers,
    reset_salad_progress,
    send_offer,
    serialize_offer,
    update_offer_content,
)
from games.daily.page.section import WORD_SALAD_GAME_ID
from games.identity import has_profile
from games.models import WordSaladOffer
from games.sections.paths import section_hub_path
from games.site.http.request_util import parse_json_object_body
from games.site.views.offers.reset import offer_reset_actors


def _offer_for_user(request, offer_id):
    return (
        WordSaladOffer.objects.select_related('task_group', 'accepted_link')
        .filter(pk=offer_id, user=request.user)
        .first()
    )


def _profile_context(profile, *, ready, missing, offers_json, profile_error=None):
    return {
        'page_title': 'Создать свой Салатик',
        'profile_ready': ready,
        'profile_missing': missing,
        'profile': profile,
        'offers_json': offers_json,
        'show_sections_nav': True,
        'profile_error': profile_error,
        'back_url': section_hub_path(WORD_SALAD_GAME_ID),
        'back_label': 'Все Салатики',
    }


@login_required
@require_http_methods(['GET', 'POST'])
def offer_salad_page(request):
    if not has_profile(request.user):
        return redirect('/accounts/login/?next=/create_salad/')
    profile = request.user.profile
    ready, missing = profile_ready_for_offers(profile)

    if request.method == 'POST' and request.POST.get('action') == 'save_profile':
        ready_after, missing_after = update_offer_profile_names(
            profile, request.POST.get('first_name'), request.POST.get('last_name'),
        )
        if not ready_after:
            return render(request, 'site/create_salad.html', _profile_context(
                profile,
                ready=False,
                missing=missing_after,
                offers_json=[],
                profile_error=offer_profile_error(missing_after),
            ))
        return redirect('new_create_salad')

    offers = list_user_offers(request.user) if ready else []
    return render(request, 'site/create_salad.html', _profile_context(
        profile,
        ready=ready,
        missing=missing,
        offers_json=[o.to_dict() for o in offers],
    ))


@login_required
@require_POST
def offer_salad_create(request):
    if not has_profile(request.user):
        return JsonResponse({'ok': False, 'error': 'Нужен профиль'}, status=403)
    body = parse_json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        offer = create_offer(request.user, kind=str(body.get('kind') or WordSaladOffer.KIND_FULL))
        row = serialize_offer(offer)
    except WordSaladOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': row.to_dict()})


@login_required
@require_http_methods(['GET', 'POST'])
def offer_salad_detail(request, offer_id):
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
            theme=str(body.get('theme') or ''),
            idea_text=str(body.get('idea_text') or ''),
            suggested_words=str(body.get('suggested_words') or ''),
            grid_text=str(body.get('grid_text') or ''),
            words_text=str(body.get('words_text') or ''),
            rare_words_text=str(body.get('rare_words_text') or ''),
            comment=str(body.get('comment') or ''),
        )
    except WordSaladOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_salad_send(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = send_offer(offer)
    except WordSaladOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@login_required
@require_POST
def offer_salad_convert(request, offer_id):
    offer = _offer_for_user(request, offer_id)
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        draft = convert_accepted_idea(offer)
    except WordSaladOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'offer': serialize_offer(draft).to_dict()})


@require_POST
def offer_salad_reset(request, offer_id):
    offer = (
        WordSaladOffer.objects.select_related('task_group')
        .filter(pk=offer_id)
        .first()
    )
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    reset_actors = offer_reset_actors(request, offer)
    if reset_actors is None:
        return JsonResponse({'ok': False, 'error': 'Нет доступа'}, status=403)
    if not offer.task_group_id:
        return JsonResponse({'ok': False, 'error': 'У идеи нет задания'}, status=400)
    task = offer.task_group.tasks.filter(number='1').first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Задание не найдено'}, status=404)
    try:
        n = sum(
            reset_salad_progress(task=task, **actor)
            for actor in reset_actors
        )
    except WordSaladOfferError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'deleted_attempts': n})
