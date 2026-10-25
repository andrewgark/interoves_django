"""Main UI: hub, games folder, profile, team."""
import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


from django.utils import timezone


from games.telegram_linking import user_has_telegram_link

from games.analytics import (
    YANDEX_GOAL_TICKET_CHECKOUT,
    analytics_ack_payload,
    ticket_purchase_goal_payload,
    yandex_goal_payload,
)
from games.models import (
    Donation,
    ProfileTeamMembership,
    Team,
    TicketRequest,
)
from games.views.util import (
    has_profile,
    has_team,
)
from games.nowpayments_util import create_invoice as nowpayments_create_invoice
from games.nowpayments_util import embed_url_for_invoice
from games.nowpayments_util import nowpayments_ipn_callback_url
from games.payment_routes import (
    CRYPTO,
    INTERNATIONAL_CARD,
    RUSSIAN_CARD,
    amount_for as ticket_amount_for,
    route_for as ticket_route_for,
    unit_price_for as ticket_unit_price_for,
)
from games.yookassa_util import configure_yookassa_from_env

from yookassa import Payment

from games.site.ui import (
    NEW_UI_PROJECT,
    _main_team_page_urls,
    _project_urls_context,
)

logger = logging.getLogger('games.views.new_ui')

@never_cache
@require_http_methods(['GET'])
def new_pay_page(request):
    """Public team-ticket checkout with login/team gating only at purchase time.

    Доступна без логина: гостю предлагаем войти, авторизованному без команды —
    создать или вступить, чтобы купить билет для команды.
    """
    team = None
    payment_teams = []
    telegram_linked = False
    if request.user.is_authenticated and has_profile(request.user):
        team = request.user.profile.team_on if has_team(request.user) else None
        payment_teams = list(
            Team.objects.filter(member_links__profile=request.user.profile)
            .distinct()
            .order_by('visible_name', 'name')
        )
        telegram_linked = user_has_telegram_link(request.user)
    recent_requests = TicketRequest.recent_for_team(team) if team else []
    ticket_price_int = ticket_unit_price_for(team, RUSSIAN_CARD)
    ticket_price_amd = ticket_unit_price_for(team, INTERNATIONAL_CARD)
    from games.tribute_config import (
        configured_product,
        merchant_public_copy,
        tribute_checkout_enabled,
    )
    from games.tribute_service import team_is_discount_eligible

    regular_product = configured_product('regular')
    discount_product = configured_product('discount')
    selected_product = discount_product if team and team_is_discount_eligible(team) else regular_product
    tribute_seller, tribute_seller_url = merchant_public_copy()
    payment_team_options = []
    for option_team in payment_teams:
        option_product = discount_product if team_is_discount_eligible(option_team) else regular_product
        payment_team_options.append({
            'team': option_team,
            'ticket_price': ticket_unit_price_for(option_team, RUSSIAN_CARD),
            'ticket_price_amd': ticket_unit_price_for(option_team, INTERNATIONAL_CARD),
            'tribute_kind': option_product.kind if option_product else '',
            'tribute_amount': str(option_product.amount_major) if option_product else '',
            'tribute_currency': option_product.currency if option_product else '',
        })
    return render(request, 'ui/pay.html', {
        'team': team,
        'payment_team_options': payment_team_options,
        'ticket_price': ticket_price_int,
        'ticket_price_display': '{:,}'.format(ticket_price_int).replace(',', ' '),
        'ticket_price_amd': ticket_price_amd,
        'ticket_price_amd_display': '{:,}'.format(ticket_price_amd).replace(',', ' '),
        'team_tickets': team.tickets if team else 0,
        'recent_ticket_requests': recent_requests,
        'telegram_linked': telegram_linked,
        'telegram_username': request.user.profile.telegram_username if telegram_linked else '',
        'tribute_enabled': tribute_checkout_enabled(),
        'tribute_product': selected_product or regular_product,
        'tribute_regular_product': regular_product,
        'tribute_discount_product': discount_product,
        'tribute_seller': tribute_seller,
        'tribute_seller_url': tribute_seller_url,
        'page_title': 'Оплата',
        **_project_urls_context(NEW_UI_PROJECT),
        **_main_team_page_urls(),
    })


def _selected_payment_team(request, *, require_explicit=False):
    if not has_profile(request.user) or not has_team(request.user):
        return None
    raw_team_id = (request.POST.get('team_id') or '').strip()
    if not raw_team_id and not require_explicit:
        return request.user.profile.team_on
    if not raw_team_id:
        return None
    membership = (
        ProfileTeamMembership.objects.select_related('team')
        .filter(profile=request.user.profile, team_id=raw_team_id)
        .first()
    )
    return membership.team if membership else None


@login_required
@require_http_methods(['POST'])
def new_telegram_link_start(request):
    from games.telegram_linking import (
        TelegramLinkError,
        create_link_token,
        sanitize_telegram_link_next,
        telegram_deep_link,
    )

    next_path = sanitize_telegram_link_next(request.POST.get('next')) or '/pay/?telegram=linked'
    fallback = '/subscription/' if next_path.startswith('/subscription') else '/pay/'
    if not has_profile(request.user):
        messages.error(request, 'Сначала создайте профиль Inter Oves.')
        return redirect(fallback)
    _row, raw_token = create_link_token(request.user, next_path=next_path)
    try:
        deep_link = telegram_deep_link(raw_token)
    except TelegramLinkError as exc:
        messages.error(request, exc.message)
        return redirect(fallback)
    return redirect(deep_link)


@require_http_methods(['POST'])
def new_create_ticket_payment(request):
    """
    Called via fetch() from /new/pay/ — must always return JSON so the client can r.json().
    (Redirects/HTML from @login_required or redirect() break fetch and show «Ошибка сети».)
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова и повторите оплату.'},
            status=401,
        )
    if not has_profile(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'profile', 'message': 'Сначала войдите и создайте профиль.'},
            status=403,
        )
    if not has_team(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'team', 'message': 'Нужно создать или вступить в команду, чтобы купить билет.'},
            status=403,
        )

    team = _selected_payment_team(request)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )
    try:
        tickets = int((request.POST.get('tickets') or '').strip())
    except Exception:
        tickets = 0
    if tickets < 1 or tickets > 20:
        return JsonResponse(
            {'status': 'error', 'reason': 'tickets', 'message': 'Введите число билетов от 1 до 20.'},
            status=400,
        )

    route = ticket_route_for(RUSSIAN_CARD)
    amount_rub = ticket_amount_for(team, route.key, tickets)

    ticket_request = None
    try:
        ticket_request = TicketRequest.objects.create(
            team=team,
            created_by=request.user,
            metrika_client_id=(request.COOKIES.get('_ym_uid') or '').strip()[:64],
            money=amount_rub,
            tickets=tickets,
            status='Pending',
            currency=route.currency,
            payment_provider=route.provider,
            merchant=route.merchant,
        )

        # YooKassa: description max 128 characters
        team_label = (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk))
        payment_description = f'Билеты для команды {team_label} (request {ticket_request.id})'
        payment_description = payment_description[:128]

        configure_yookassa_from_env()
        payment = Payment.create({
            'amount': {
                'value': f'{amount_rub:.2f}',
                'currency': 'RUB',
            },
            'confirmation': {
                'type': 'embedded',
            },
            'capture': True,
            'description': payment_description,
            'metadata': {
                'ticket_request_id': str(ticket_request.id),
                'team_id': str(team.pk),
                'tickets': str(tickets),
                'kind': 'team_ticket',
            },
        }, uuid.uuid4().hex)
        payment_data = dict(payment)
        ticket_request.yookassa_id = payment_data.get('id') or ticket_request.yookassa_id
        ticket_request.save(update_fields=['yookassa_id'])
        confirmation_token = (payment_data.get('confirmation') or {}).get('confirmation_token')
        if not confirmation_token:
            raise RuntimeError('Missing confirmation_token from YooKassa')
    except RuntimeError as exc:
        if ticket_request is not None and 'Missing YooKassa credentials' in str(exc):
            logger.error('new_create_ticket_payment: %s', exc)
        else:
            logger.exception(
                'new_create_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
                getattr(ticket_request, 'id', None),
                team.pk,
                amount_rub,
            )
        if 'Missing YooKassa credentials' in str(exc):
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'yookassa_config',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=503,
            )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'order',
                    'message': 'Не удалось создать заказ. Попробуйте позже.',
                },
                status=500,
            )
        return JsonResponse(
            {'status': 'error', 'reason': 'yookassa', 'message': 'Не получилось создать платёж. Попробуйте позже.'},
            status=502,
        )
    except Exception:
        logger.exception(
            'new_create_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
        )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'db',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=500,
            )
        return JsonResponse(
            {'status': 'error', 'reason': 'yookassa', 'message': 'Не получилось создать платёж. Попробуйте позже.'},
            status=502,
        )

    return JsonResponse({
        'status': 'ok',
        'confirmation_token': confirmation_token,
        'return_url': request.build_absolute_uri('/pay/?payment=return'),
        'ticket_request_id': ticket_request.id,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(ticket_request.id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, ticket_request.id),
            ),
        ],
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket_request.id})
        ),
    })


@require_http_methods(['POST'])
def new_create_crypto_ticket_payment(request):
    """Create TicketRequest + NOWPayments invoice; returns embed URL for crypto widget."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова и повторите оплату.'},
            status=401,
        )
    if not has_profile(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'profile', 'message': 'Сначала войдите и создайте профиль.'},
            status=403,
        )
    if not has_team(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'team', 'message': 'Нужно создать или вступить в команду, чтобы купить билет.'},
            status=403,
        )

    team = _selected_payment_team(request)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )
    try:
        tickets = int((request.POST.get('tickets') or '').strip())
    except Exception:
        tickets = 0
    if tickets < 1 or tickets > 20:
        return JsonResponse(
            {'status': 'error', 'reason': 'tickets', 'message': 'Введите число билетов от 1 до 20.'},
            status=400,
        )

    route = ticket_route_for(CRYPTO)
    amount_rub = ticket_amount_for(team, route.key, tickets)

    ticket_request = None
    try:
        ticket_request = TicketRequest.objects.create(
            team=team,
            created_by=request.user,
            metrika_client_id=(request.COOKIES.get('_ym_uid') or '').strip()[:64],
            money=amount_rub,
            tickets=tickets,
            status='Pending',
            currency=route.currency,
            payment_provider=route.provider,
            merchant=route.merchant,
        )

        team_label = (getattr(team, 'visible_name', None) or getattr(team, 'name', None) or str(team.pk))
        payment_description = f'Билеты для команды {team_label} (request {ticket_request.id})'
        return_url = request.build_absolute_uri('/pay/?payment=crypto_return')
        ipn_url = nowpayments_ipn_callback_url()

        invoice = nowpayments_create_invoice(
            price_amount=amount_rub,
            price_currency='rub',
            order_id=str(ticket_request.id),
            order_description=payment_description,
            ipn_callback_url=ipn_url,
            success_url=return_url,
            cancel_url=return_url,
        )
        invoice_id = invoice.get('id') or invoice.get('invoice_id')
        ticket_request.nowpayments_id = str(invoice_id)
        ticket_request.save(update_fields=['nowpayments_id'])
        invoice_url = invoice.get('invoice_url') or ''
        embed = embed_url_for_invoice(invoice_id)
    except RuntimeError as exc:
        msg = str(exc)
        if ticket_request is not None and 'Missing NOWPayments' in msg:
            logger.error('new_create_crypto_ticket_payment: %s', exc)
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'nowpayments_config',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=503,
            )
        logger.exception(
            'new_create_crypto_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
        )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'order',
                    'message': 'Не удалось создать заказ. Попробуйте позже.',
                },
                status=500,
            )
        # Avoid HTTP 502: some proxies replace the JSON body with an HTML gateway page.
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )
    except Exception:
        logger.exception(
            'new_create_crypto_ticket_payment failed ticket_request_id=%s team_id=%s amount_rub=%s',
            getattr(ticket_request, 'id', None),
            team.pk,
            amount_rub,
        )
        if ticket_request is None:
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'db',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )

    return JsonResponse({
        'status': 'ok',
        'invoice_id': str(invoice_id),
        'invoice_url': invoice_url,
        'embed_url': embed,
        'return_url': return_url,
        'ticket_request_id': ticket_request.id,
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(ticket_request.id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, ticket_request.id),
            ),
        ],
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': ticket_request.id})
        ),
    })


@require_http_methods(['POST'])
def new_create_tribute_ticket_payment(request):
    """Create/reuse one unambiguous Digital Product intent and return its fixed webLink."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {'status': 'error', 'reason': 'login', 'message': 'Сессия истекла. Войдите снова.'},
            status=401,
        )
    if not has_profile(request.user) or not has_team(request.user):
        return JsonResponse(
            {'status': 'error', 'reason': 'team', 'message': 'Нужен профиль и команда.'},
            status=403,
        )
    try:
        tickets = int(request.POST.get('tickets') or '0')
    except (TypeError, ValueError):
        tickets = 0
    if tickets != 1:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'quantity',
                'message': 'Через Tribute одна покупка начисляет ровно один билет.',
            },
            status=400,
        )
    team = _selected_payment_team(request, require_explicit=True)
    if team is None:
        return JsonResponse(
            {'status': 'error', 'reason': 'team_forbidden', 'message': 'Выбранная команда недоступна.'},
            status=403,
        )

    from games.tribute_service import TributeCheckoutError, create_or_reuse_intent

    try:
        intent, product, reused = create_or_reuse_intent(user=request.user, team=team)
    except TributeCheckoutError as exc:
        return JsonResponse(
            {'status': 'error', 'reason': exc.reason, 'message': exc.message},
            status=exc.status,
        )
    if not intent.ticket_request.metrika_client_id:
        intent.ticket_request.metrika_client_id = (request.COOKIES.get('_ym_uid') or '').strip()[:64]
        intent.ticket_request.save(update_fields=['metrika_client_id'])
    logger.info(
        'tribute_redirect intent_id=%s user_id=%s team_id=%s product_id=%s reused=%s',
        intent.pk, request.user.pk, team.pk, product.product_id, reused,
    )
    return JsonResponse({
        'status': 'ok',
        'payment_url': product.web_url,
        'intent_id': intent.pk,
        'ticket_request_id': intent.ticket_request_id,
        'reused': reused,
        'status_url': request.build_absolute_uri(
            reverse('new_ticket_payment_status', kwargs={'ticket_request_id': intent.ticket_request_id})
        ),
        'analytics_events': [
            yandex_goal_payload(
                YANDEX_GOAL_TICKET_CHECKOUT,
                key='ticket_checkout:{}'.format(intent.ticket_request_id),
                ack=analytics_ack_payload(YANDEX_GOAL_TICKET_CHECKOUT, intent.ticket_request_id),
            ),
        ],
    })

@require_http_methods(['GET', 'POST'])
def new_ticket_payment_status(request, ticket_request_id):
    """JSON status for a team's TicketRequest (long-polling friendly for crypto)."""
    if not request.user.is_authenticated or not has_profile(request.user) or not has_team(request.user):
        return JsonResponse({'status': 'error', 'reason': 'auth'}, status=401)

    ticket_request = (
        TicketRequest.objects.select_related('team')
        .filter(
            id=ticket_request_id,
            team__member_links__profile=request.user.profile,
        )
        .distinct()
        .first()
    )
    if not ticket_request:
        return JsonResponse({'status': 'error', 'reason': 'not_found'}, status=404)

    if request.method == 'POST':
        ack_key = (
            request.POST.get('analytics_ack')
            or request.headers.get('X-Interoves-Analytics-Ack')
            or ''
        ).strip()
        expected_key = 'ticket_purchase:{}'.format(ticket_request.id)
        if ack_key != expected_key:
            return JsonResponse({'status': 'error', 'reason': 'invalid_ack'}, status=400)
        if ticket_request.status != 'Accepted':
            return JsonResponse({'status': 'error', 'reason': 'not_accepted'}, status=409)
        if ticket_request.purchase_goal_sent_at is None:
            ticket_request.purchase_goal_sent_at = timezone.now()
            ticket_request.save(update_fields=['purchase_goal_sent_at'])
        return JsonResponse({'status': 'ok'})

    payload = {
        'status': ticket_request.status,
        'ticket_request_id': ticket_request.id,
        'tickets': ticket_request.tickets,
        'money': ticket_request.money,
        'currency': ticket_request.currency,
        'payment_provider': ticket_request.payment_provider,
        'merchant': ticket_request.merchant,
    }
    if ticket_request.status == 'Accepted' and ticket_request.purchase_goal_sent_at is None:
        payload['analytics_events'] = [ticket_purchase_goal_payload(ticket_request)]
    if ticket_request.status == 'Accepted' and ticket_request.team is not None:
        payload['team_tickets'] = ticket_request.team.tickets
    return JsonResponse(payload)


def new_donate_page(request):
    from games.donation_service import recent_donations_for_request, reject_stale_pending_donations

    reject_stale_pending_donations()
    return render(request, 'new/donate.html', {
        'recent_donations': recent_donations_for_request(request),
    })


@require_http_methods(['POST'])
def new_create_crypto_donation(request):
    """Create Donation + NOWPayments invoice; public (login optional)."""
    from games.donation_service import MIN_DONATION_RUB, donation_order_id, remember_donation_in_session

    raw = (request.POST.get('amount_rub') or request.POST.get('amount') or '').strip().replace(',', '.')
    try:
        amount_rub = int(float(raw))
    except (TypeError, ValueError):
        amount_rub = 0
    if amount_rub < MIN_DONATION_RUB:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'amount',
                'message': 'Минимальная сумма доната — {} ₽.'.format(MIN_DONATION_RUB),
            },
            status=400,
        )
    if amount_rub > 1_000_000:
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'amount',
                'message': 'Слишком большая сумма. Укажите меньше миллиона рублей.',
            },
            status=400,
        )

    donation = None
    try:
        user = request.user if request.user.is_authenticated else None
        donation = Donation.objects.create(
            amount_rub=amount_rub,
            status='Pending',
            user=user,
        )
        remember_donation_in_session(request, donation.id)
        return_url = request.build_absolute_uri('/donate/?payment=crypto_return')
        ipn_url = nowpayments_ipn_callback_url()
        invoice = nowpayments_create_invoice(
            price_amount=amount_rub,
            price_currency='rub',
            order_id=donation_order_id(donation.id),
            order_description='Донат Inter Oves #{}'.format(donation.id),
            ipn_callback_url=ipn_url,
            success_url=return_url,
            cancel_url=return_url,
        )
        invoice_id = invoice.get('id') or invoice.get('invoice_id')
        donation.nowpayments_id = str(invoice_id)
        donation.save(update_fields=['nowpayments_id'])
        invoice_url = invoice.get('invoice_url') or ''
        embed = embed_url_for_invoice(invoice_id)
    except RuntimeError as exc:
        msg = str(exc)
        if donation is not None and 'Missing NOWPayments' in msg:
            logger.error('new_create_crypto_donation: %s', exc)
            return JsonResponse(
                {
                    'status': 'error',
                    'reason': 'nowpayments_config',
                    'message': 'Сейчас не получается создать платёж. Напишите Андрею в Telegram: https://t.me/andrewgark',
                },
                status=503,
            )
        logger.exception(
            'new_create_crypto_donation failed donation_id=%s amount_rub=%s',
            getattr(donation, 'id', None),
            amount_rub,
        )
        if donation is None:
            return JsonResponse(
                {'status': 'error', 'reason': 'order', 'message': 'Не удалось создать донат. Попробуйте позже.'},
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )
    except Exception:
        logger.exception(
            'new_create_crypto_donation failed donation_id=%s amount_rub=%s',
            getattr(donation, 'id', None),
            amount_rub,
        )
        if donation is None:
            return JsonResponse(
                {'status': 'error', 'reason': 'db', 'message': 'Не удалось сохранить донат. Попробуйте позже.'},
                status=500,
            )
        return JsonResponse(
            {
                'status': 'error',
                'reason': 'nowpayments',
                'message': 'Не получилось создать крипто-платёж. Попробуйте позже.',
            },
            status=503,
        )

    return JsonResponse({
        'status': 'ok',
        'invoice_id': str(invoice_id),
        'invoice_url': invoice_url,
        'embed_url': embed,
        'return_url': return_url,
        'donation_id': donation.id,
        'amount_rub': donation.amount_rub,
        'donation_status': donation.status,
        'created_at': donation.created_at.strftime('%d.%m %H:%M') if donation.created_at else '',
        'public_token': donation.public_token,
        'status_url': request.build_absolute_uri(
            reverse('donate_status', kwargs={'public_token': donation.public_token})
        ),
    })


@require_http_methods(['GET'])
def new_donation_status(request, public_token):
    """Public JSON status for a donation (token acts as capability URL)."""
    donation = Donation.objects.filter(public_token=public_token).first()
    if not donation:
        return JsonResponse({'status': 'error', 'reason': 'not_found'}, status=404)

    payload = {
        'status': donation.status,
        'donation_id': donation.id,
        'amount_rub': donation.amount_rub,
    }
    if donation.status == 'Confirmed':
        payload['pay_amount'] = donation.pay_amount
        payload['pay_currency'] = donation.pay_currency
    return JsonResponse(payload)



__all__ = [
    'new_pay_page',
    '_selected_payment_team',
    'new_telegram_link_start',
    'new_create_ticket_payment',
    'new_create_crypto_ticket_payment',
    'new_create_tribute_ticket_payment',
    'new_ticket_payment_status',
    'new_donate_page',
    'new_create_crypto_donation',
    'new_donation_status',
]
