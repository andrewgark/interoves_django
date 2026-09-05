"""Hidden Club subscription checkout at /subscription/."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from games.analytics import (
    YANDEX_GOAL_SUBSCRIPTION_CHECKOUT,
    YANDEX_GOAL_SUBSCRIPTION_VIEW,
    queue_pending_goal,
    yandex_goal_payload,
)
from games.club_access import get_club_subscription, has_club_access
from games.models import ClubSubscription
from games.telegram_linking import user_has_telegram_link
from games.tribute_config import (
    club_checkout_enabled,
    club_management_url,
    configured_club_product,
    merchant_public_copy,
)
from games.views.new_ui import NEW_UI_PROJECT, _project_urls_context
from games.views.util import has_profile

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo('Europe/Moscow')


def _user_tz(request):
    try:
        profile = getattr(request.user, 'profile', None)
        name = getattr(profile, 'timezone', None) or 'Europe/Moscow'
        return ZoneInfo(name)
    except Exception:
        return MOSCOW


def format_club_date(dt, tz) -> str:
    if dt is None:
        return ''
    local = timezone.localtime(dt, tz)
    return date_format(local, 'j E Y')


def _display_status(subscription: ClubSubscription | None, *, now=None) -> str:
    if subscription is None:
        return 'none'
    return subscription.effective_status(now)


def _price_label(subscription: ClubSubscription | None) -> str:
    if subscription is None or not subscription.currency or not subscription.amount:
        return ''
    major = subscription.amount / 100
    if major == int(major):
        display = '{:,.0f}'.format(major).replace(',', ' ')
    else:
        display = '{:,.2f}'.format(major).replace(',', ' ')
    if subscription.currency == 'RUB':
        return '{} ₽ в месяц'.format(display)
    if subscription.currency == 'USD':
        return '${} в месяц'.format(display)
    return '{} {} в месяц'.format(display, subscription.currency)


def _subscription_page_context(request):
    telegram_linked = False
    if request.user.is_authenticated and has_profile(request.user):
        telegram_linked = user_has_telegram_link(request.user)
    subscription = get_club_subscription(request.user) if request.user.is_authenticated else None
    now = timezone.now()
    status = _display_status(subscription, now=now)
    tz = _user_tz(request)
    paid_until_label = format_club_date(subscription.paid_until, tz) if subscription else ''
    show_next_charge = bool(
        status == ClubSubscription.STATUS_ACTIVE
        and subscription
        and subscription.paid_until
    )
    rub = configured_club_product('rub')
    usd = configured_club_product('usd')
    seller, seller_url = merchant_public_copy()
    show_checkout = status in ('none', ClubSubscription.STATUS_EXPIRED)
    return {
        'page_title': 'Клубная подписка',
        'robots_noindex': True,
        'telegram_linked': telegram_linked,
        'telegram_username': (
            request.user.profile.telegram_username
            if telegram_linked else ''
        ),
        'club_status': status,
        'has_club_access': has_club_access(request.user, now=now),
        'club_subscription': subscription,
        'paid_until_label': paid_until_label,
        'next_charge_label': paid_until_label if show_next_charge else '',
        'price_label': _price_label(subscription),
        'show_checkout': show_checkout,
        'club_checkout_enabled': club_checkout_enabled(),
        'club_rub': rub,
        'club_usd': usd,
        'club_management_url': club_management_url(),
        'tribute_seller': seller,
        'tribute_seller_url': seller_url,
        'telegram_linked_notice': request.GET.get('telegram') == 'linked',
        **_project_urls_context(NEW_UI_PROJECT),
    }


@never_cache
@require_http_methods(['GET'])
def subscription_page(request):
    queue_pending_goal(
        request,
        YANDEX_GOAL_SUBSCRIPTION_VIEW,
        params={'provider': 'tribute'},
        key='subscription_view',
    )
    return render(request, 'ui/subscription.html', _subscription_page_context(request))


@require_http_methods(['POST'])
def subscription_checkout(request):
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова.'},
            status=401,
        )
    if not has_profile(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'profile', 'message': 'Сначала создайте профиль Inter Oves.'},
            status=403,
        )
    if not user_has_telegram_link(request.user):
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'telegram_unlinked',
                'message': 'Сначала привяжите Telegram, чтобы Tribute мог открыть клубный доступ.',
            },
            status=409,
        )
    if not club_checkout_enabled():
        return JsonResponse(
            {'status': 'error', 'reason': 'club_config', 'message': 'Оформление клубной подписки пока не настроено.'},
            status=503,
        )
    status = _display_status(get_club_subscription(request.user))
    if status in (ClubSubscription.STATUS_ACTIVE, ClubSubscription.STATUS_CANCELLED):
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'already_subscribed',
                'message': 'Подписка уже оформлена. Управлять ей можно в Tribute.',
            },
            status=409,
        )
    currency = str(request.POST.get('currency') or '').strip().lower()
    if currency not in ('rub', 'usd'):
        return JsonResponse(
            {'status': 'error', 'reason': 'currency', 'message': 'Выберите российскую или иностранную карту.'},
            status=400,
        )
    product = configured_club_product(currency)
    if product is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'club_config', 'message': 'Этот вариант оплаты пока не настроен.'},
            status=503,
        )
    logger.info(
        'club_checkout_start user_id=%s currency=%s subscription_id=%s',
        request.user.pk, product.currency, product.subscription_id,
    )
    return JsonResponse({
        'status': 'ok',
        'payment_url': product.web_url,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_SUBSCRIPTION_CHECKOUT,
                params={
                    'provider': 'tribute',
                    'currency': product.currency.lower(),
                    'amount': product.amount,
                },
                key='subscription_checkout_start:{}:{}'.format(request.user.pk, product.currency),
            ),
        ],
    })
