"""Hidden Club subscription checkout at /subscription/."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from django.contrib import messages
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
from games.club_yookassa import (
    AMOUNT_ANNUAL_KOPECKS,
    AMOUNT_INTRO_KOPECKS,
    AMOUNT_MONTHLY_KOPECKS,
    add_calendar_months,
    cancel_yookassa_subscription,
    club_yookassa_enabled,
    detach_yookassa_payment_method,
    initial_monthly_amount_kopecks,
    intro_available,
    renewal_pending_at_detach,
    resume_yookassa_subscription,
    start_annual_subscription,
    start_monthly_subscription,
    yookassa_recurring_enabled,
)
from games.models import ClubSubscription, SavedPaymentMethod
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


def _format_minor_amount(amount: int, currency: str) -> str:
    major = amount / 100
    display = '{:,.0f}'.format(major) if major == int(major) else '{:,.2f}'.format(major)
    display = display.replace(',', ' ')
    return '{} {}'.format(display, currency)


def _annual_saving_percent(monthly_amount: int, annual_amount: int) -> int:
    regular_year_amount = monthly_amount * 12
    if annual_amount <= 0 or annual_amount >= regular_year_amount:
        return 0
    return round((regular_year_amount - annual_amount) * 100 / regular_year_amount)


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
    if subscription.plan == ClubSubscription.PLAN_ANNUAL and subscription.currency == 'RUB':
        return '{} ₽ за год'.format(display)
    if subscription.currency == 'RUB':
        if subscription.provider == ClubSubscription.PROVIDER_YOOKASSA:
            return '{} ₽ в месяц'.format('{:,.0f}'.format(AMOUNT_MONTHLY_KOPECKS / 100).replace(',', ' '))
        return '{} ₽ в месяц'.format(display)
    if subscription.currency == 'EUR':
        return '€{} в месяц'.format(display)
    if subscription.currency == 'USD':
        return '${} в месяц'.format(display)
    return '{} {} в месяц'.format(display, subscription.currency)


def _subscription_page_context(request):
    if (request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
            and request.GET.get('approval_preview') == '1'):
        # Presentation-only values: do not load credentials or construct model
        # instances. Preview must work even before billing has been enabled.
        return {
            'page_title': 'Клубная подписка',
            'robots_noindex': True,
            'approval_preview': True,
            'saved_payment_method_label': 'Банковская карта •••• 4242',
            'has_club_access': True,  # Display only; no entitlement is granted.
            'paid_until_label': format_club_date(
                add_calendar_months(timezone.now(), 1), _user_tz(request),
            ),
            **_project_urls_context(NEW_UI_PROJECT),
        }
    telegram_linked = False
    if request.user.is_authenticated and has_profile(request.user):
        telegram_linked = user_has_telegram_link(request.user)
    subscription = get_club_subscription(request.user) if request.user.is_authenticated else None
    now = timezone.now()
    status = _display_status(subscription, now=now)
    tz = _user_tz(request)
    paid_until_label = format_club_date(subscription.paid_until, tz) if subscription else ''
    is_yookassa = bool(
        subscription and subscription.provider == ClubSubscription.PROVIDER_YOOKASSA
    )
    is_monthly = bool(subscription and subscription.plan == ClubSubscription.PLAN_MONTHLY)
    is_annual = bool(subscription and subscription.plan == ClubSubscription.PLAN_ANNUAL)
    show_next_charge = bool(
        status == ClubSubscription.STATUS_ACTIVE
        and subscription
        and subscription.paid_until
        and subscription.auto_renew
        and not is_annual
    )
    next_charge_amount_label = ''
    if show_next_charge and is_yookassa and is_monthly:
        next_charge_amount_label = '900 ₽ — {}'.format(paid_until_label)
    eur = configured_club_product('eur')
    seller, seller_url = merchant_public_copy()
    show_checkout = status in ('none', ClubSubscription.STATUS_EXPIRED, ClubSubscription.STATUS_PENDING)
    if status == ClubSubscription.STATUS_PENDING and subscription and subscription.grants_access(now):
        show_checkout = False
    intro = intro_available(subscription)
    monthly_amount = initial_monthly_amount_kopecks(subscription)
    monthly_intro_label = _format_minor_amount(AMOUNT_INTRO_KOPECKS, '₽')
    monthly_regular_label = _format_minor_amount(AMOUNT_MONTHLY_KOPECKS, '₽')
    annual_label = _format_minor_amount(AMOUNT_ANNUAL_KOPECKS, '₽')
    tribute_annual_saving_percent = (
        _annual_saving_percent(eur.amount, eur.yearly_amount)
        if eur and eur.yearly_amount else 0
    )
    yk_enabled = club_yookassa_enabled()
    saved_method = (SavedPaymentMethod.objects.filter(
        user=request.user, provider='yookassa', is_active=True,
        provider_payment_method_id__isnull=False,
    ).exclude(provider_payment_method_id='').first() if request.user.is_authenticated else None)
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
        'next_charge_amount_label': next_charge_amount_label,
        'price_label': _price_label(subscription),
        'show_checkout': show_checkout,
        'club_checkout_enabled': club_checkout_enabled(),
        'club_yookassa_enabled': yk_enabled,
        'yookassa_recurring_enabled': yookassa_recurring_enabled(),
        'club_eur': eur,
        'club_eur_annual_saving_percent': tribute_annual_saving_percent,
        'club_management_url': club_management_url(),
        'tribute_seller': seller,
        'tribute_seller_url': seller_url,
        'telegram_linked_notice': request.GET.get('telegram') == 'linked',
        'payment_return': request.GET.get('payment') == 'return',
        'is_yookassa': is_yookassa,
        'is_monthly_plan': is_monthly,
        'saved_payment_method_label': saved_method.display_name if saved_method else '',
        'payment_method_detached': bool(
            subscription and subscription.payment_method_detached_at and not saved_method
        ),
        'renewal_pending_at_detach': renewal_pending_at_detach(subscription),
        'can_resume_yookassa': bool(saved_method and subscription
                                    and saved_method.method_type == 'bank_card'
                                    and subscription.saved_payment_method_id == saved_method.pk),
        'is_annual_plan': is_annual,
        'intro_available': intro,
        'monthly_amount_kopecks': monthly_amount,
        'monthly_intro_kopecks': AMOUNT_INTRO_KOPECKS,
        'monthly_regular_kopecks': AMOUNT_MONTHLY_KOPECKS,
        'annual_kopecks': AMOUNT_ANNUAL_KOPECKS,
        'monthly_intro_label': monthly_intro_label,
        'monthly_regular_label': monthly_regular_label,
        'annual_label': annual_label,
        'yookassa_annual_saving_percent': _annual_saving_percent(
            AMOUNT_MONTHLY_KOPECKS, AMOUNT_ANNUAL_KOPECKS,
        ),
        'monthly_cta_label': 'Подписаться за {}'.format(
            monthly_intro_label if intro else monthly_regular_label,
        ),
        **_project_urls_context(NEW_UI_PROJECT),
    }


@never_cache
@require_http_methods(['GET'])
def subscription_page(request):
    context = _subscription_page_context(request)
    if not context.get('approval_preview'):
        queue_pending_goal(
            request,
            YANDEX_GOAL_SUBSCRIPTION_VIEW,
            params={'provider': 'club'},
            key='subscription_view',
        )
    return render(request, 'ui/subscription.html', context)


def _auth_json_guard(request):
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
    return None


@require_http_methods(['POST'])
def subscription_checkout(request):
    """Tribute EUR (and optional Tribute RUB) checkout — unchanged path."""
    guard = _auth_json_guard(request)
    if guard is not None:
        return guard
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
                'message': 'Подписка уже оформлена.',
            },
            status=409,
        )
    currency = str(request.POST.get('currency') or '').strip().lower()
    if currency not in ('rub', 'eur'):
        return JsonResponse(
            {'status': 'error', 'reason': 'currency', 'message': 'Выберите способ оплаты.'},
            status=400,
        )
    if currency == 'rub':
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'use_yookassa',
                'message': 'Российская карта оплачивается через ЮKassa.',
            },
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


@require_http_methods(['POST'])
def subscription_yookassa_monthly_start(request):
    guard = _auth_json_guard(request)
    if guard is not None:
        return guard
    return_url = request.build_absolute_uri('/subscription/?payment=return')
    result = start_monthly_subscription(request.user, return_url=return_url)
    if not result.ok:
        return JsonResponse(
            {'status': 'error', 'reason': result.reason, 'message': result.message},
            status=result.http_status,
        )
    amount = result.payment.amount if result.payment else initial_monthly_amount_kopecks(
        get_club_subscription(request.user),
    )
    return JsonResponse({
        'status': 'ok',
        'payment_url': result.confirmation_url,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_SUBSCRIPTION_CHECKOUT,
                params={'provider': 'yookassa', 'plan': 'monthly', 'amount': amount},
                key='subscription_yk_monthly:{}:{}'.format(
                    request.user.pk, getattr(result.payment, 'pk', 'x'),
                ),
            ),
        ],
    })


@require_http_methods(['POST'])
def subscription_yookassa_annual_start(request):
    guard = _auth_json_guard(request)
    if guard is not None:
        return guard
    return_url = request.build_absolute_uri('/subscription/?payment=return')
    result = start_annual_subscription(request.user, return_url=return_url)
    if not result.ok:
        return JsonResponse(
            {'status': 'error', 'reason': result.reason, 'message': result.message},
            status=result.http_status,
        )
    return JsonResponse({
        'status': 'ok',
        'payment_url': result.confirmation_url,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_SUBSCRIPTION_CHECKOUT,
                params={'provider': 'yookassa', 'plan': 'annual', 'amount': AMOUNT_ANNUAL_KOPECKS},
                key='subscription_yk_annual:{}:{}'.format(
                    request.user.pk, getattr(result.payment, 'pk', 'x'),
                ),
            ),
        ],
    })


@require_http_methods(['POST'])
@never_cache
def subscription_payment_method_detach(request):
    # No profile or feature-flag prerequisite: every authenticated owner can detach.
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'reason': 'login'}, status=401)
    result = detach_yookassa_payment_method(request.user)
    subscription = ClubSubscription.objects.filter(user=request.user).first()
    message = result.message
    if subscription and subscription.grants_access():
        message += ' Доступ к подписке сохранится до {}.'.format(
            format_club_date(subscription.paid_until, _user_tz(request)),
        )
    messages.success(request, message)
    return JsonResponse({'status': 'ok', 'message': message})


@require_http_methods(['POST'])
def subscription_yookassa_cancel(request):
    guard = _auth_json_guard(request)
    if guard is not None:
        return guard
    result = cancel_yookassa_subscription(request.user)
    if not result.ok:
        return JsonResponse(
            {'status': 'error', 'reason': result.reason, 'message': result.message},
            status=result.http_status,
        )
    return JsonResponse({'status': 'ok', 'message': result.message})


@require_http_methods(['POST'])
def subscription_yookassa_resume(request):
    guard = _auth_json_guard(request)
    if guard is not None:
        return guard
    result = resume_yookassa_subscription(request.user)
    if not result.ok:
        return JsonResponse(
            {'status': 'error', 'reason': result.reason, 'message': result.message},
            status=result.http_status,
        )
    return JsonResponse({'status': 'ok', 'message': result.message})
