import json
import logging

from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from games.exception import InvalidFormException
from games.forms import TicketRequestForm
from games.models import TicketRequest
from games.nowpayments_util import verify_ipn_signature
from games.ticket_service import accept_ticket_request, reject_ticket_request
from games.tribute_util import verify_webhook_signature as verify_tribute_webhook_signature
from games.views.util import has_team, redirect_to_referer
from games.yookassa_util import configure_yookassa_from_env

from yookassa import Payment

logger = logging.getLogger(__name__)

_TICKET_CUSTOMER_PREFIX = 'ticket:'


def _parse_ticket_customer_id(customer_id) -> int | None:
    if not customer_id:
        return None
    raw = str(customer_id).strip()
    if not raw.startswith(_TICKET_CUSTOMER_PREFIX):
        return None
    try:
        return int(raw[len(_TICKET_CUSTOMER_PREFIX):])
    except (TypeError, ValueError):
        return None


def _resolve_ticket_request_for_tribute(payload: dict):
    """Find TicketRequest by tribute order uuid, then by customerId ticket:{id}."""
    order_uuid = payload.get('uuid')
    if order_uuid:
        ticket = (
            TicketRequest.objects.select_for_update()
            .select_related('team')
            .filter(tribute_id=str(order_uuid))
            .first()
        )
        if ticket is not None:
            return ticket

    ticket_id = _parse_ticket_customer_id(payload.get('customerId'))
    if ticket_id is None:
        return None
    return (
        TicketRequest.objects.select_for_update()
        .select_related('team')
        .filter(id=ticket_id)
        .first()
    )


@user_passes_test(has_team)
def request_ticket(request):
    """
    Legacy endpoint used by old UI.

    It only creates a TicketRequest in DB (Pending). Actual payment confirmation must happen
    via YooKassa webhook flow; polling is intentionally removed.
    """
    team = request.user.profile.team_on
    form = TicketRequestForm(request.POST)
    if not form.is_valid():
        raise InvalidFormException('Введите число билетов от 1 до 20')
    ticket_request = form.save(commit=True)
    ticket_request.team = team
    ticket_request.save()
    return redirect_to_referer(request)


def check_order(request):
    """
    Legacy webhook endpoint (was used with description tricks).
    Kept for backward compatibility; new integration should use yookassa_webhook().
    """
    event_json = json.loads(request.body)
    if event_json['event'] != "payment.succeeded":
        return HttpResponse(status=200)
    ticket_request = get_object_or_404(TicketRequest, yookassa_id=event_json['description'])
    with transaction.atomic():
        locked = TicketRequest.objects.select_for_update().select_related('team').get(pk=ticket_request.pk)
        accept_ticket_request(locked, source='legacy_check_order')
    return HttpResponse(status=200)


@csrf_exempt
def yookassa_webhook(request):
    """
    Main webhook endpoint for YooKassa.
    Expected events: payment.succeeded / payment.canceled.

    Idempotent: can be safely retried by YooKassa.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        event_json = json.loads(request.body)
    except Exception:
        return HttpResponse(status=400)

    event = event_json.get('event')
    if event not in ('payment.succeeded', 'payment.canceled', 'payment.waiting_for_capture'):
        return HttpResponse(status=200)

    payment_obj = (event_json.get('object') or {})
    payment_id = payment_obj.get('id')
    if not payment_id:
        logger.warning('yookassa_webhook: missing payment id event=%s', event)
        return HttpResponse(status=200)

    try:
        configure_yookassa_from_env()
        payment = Payment.find_one(payment_id)
        payment_data = dict(payment)
    except Exception:
        logger.exception(
            'yookassa_webhook: Payment.find_one failed payment_id=%s event=%s',
            payment_id,
            event,
        )
        # Don't 500 on temporary API failures; YooKassa will retry.
        return HttpResponse(status=200)

    if event == 'payment.waiting_for_capture':
        # We normally create payments with capture=True, but handle this event anyway.
        try:
            Payment.capture(payment_id, {'amount': payment_data.get('amount')})
        except Exception:
            logger.exception(
                'yookassa_webhook: Payment.capture failed payment_id=%s',
                payment_id,
            )
            return HttpResponse(status=200)
        return HttpResponse(status=200)

    metadata = payment_data.get('metadata') or {}
    ticket_request_id = metadata.get('ticket_request_id')
    if not ticket_request_id:
        logger.warning(
            'yookassa_webhook: missing metadata.ticket_request_id payment_id=%s event=%s description=%r',
            payment_id,
            event,
            payment_data.get('description'),
        )
        return HttpResponse(status=200)

    notify_event = None
    with transaction.atomic():
        ticket_request = (
            TicketRequest.objects.select_for_update()
            .select_related('team')
            .filter(id=ticket_request_id)
            .first()
        )
        if not ticket_request:
            logger.warning(
                'yookassa_webhook: ticket request not found payment_id=%s ticket_request_id=%s event=%s',
                payment_id,
                ticket_request_id,
                event,
            )
            return HttpResponse(status=200)

        if event == 'payment.canceled':
            result = reject_ticket_request(ticket_request, source='webhook')
            if result.changed:
                notify_event = event
        else:
            result = accept_ticket_request(
                ticket_request,
                yookassa_id=payment_id,
                source='webhook',
            )
            if result.changed:
                notify_event = event

    if notify_event:
        from games.telegram.notify import notify_payment_event

        ticket_request = TicketRequest.objects.filter(id=ticket_request_id).first()
        if ticket_request is not None:
            transaction.on_commit(lambda tr=ticket_request, ev=notify_event: notify_payment_event(tr, ev))

    return HttpResponse(status=200)


def _nowpayments_handle_donation_ipn(event_json, *, payment_status, order_id, np_id, payment_id):
    from games.donation_service import (
        confirm_donation,
        extract_pay_fields,
        parse_donation_order_id,
        reject_donation,
    )
    from games.models import Donation

    donation_id = parse_donation_order_id(order_id)
    if donation_id is None:
        logger.warning(
            'nowpayments_ipn: invalid donation order_id=%s payment_id=%s status=%s',
            order_id,
            payment_id,
            payment_status,
        )
        return HttpResponse(status=200)

    notify_event = None
    with transaction.atomic():
        donation = Donation.objects.select_for_update().filter(id=donation_id).first()
        if not donation:
            logger.warning(
                'nowpayments_ipn: donation not found order_id=%s payment_id=%s status=%s',
                order_id,
                payment_id,
                payment_status,
            )
            return HttpResponse(status=200)

        if payment_status == 'finished':
            pay_amount, pay_currency = extract_pay_fields(event_json)
            result = confirm_donation(
                donation,
                pay_amount=pay_amount or None,
                pay_currency=pay_currency or None,
                nowpayments_id=np_id,
                source='nowpayments_ipn',
            )
            if result.changed:
                notify_event = 'donation.confirmed'
        elif payment_status in ('failed', 'expired'):
            result = reject_donation(donation, source='nowpayments_ipn')
            if result.changed:
                notify_event = 'donation.rejected'
        else:
            logger.info(
                'nowpayments_ipn: ignore donation status=%s order_id=%s payment_id=%s',
                payment_status,
                order_id,
                payment_id,
            )

    if notify_event:
        from games.telegram.notify import notify_donation_event

        donation = Donation.objects.filter(id=donation_id).first()
        if donation is not None:
            transaction.on_commit(
                lambda d=donation, ev=notify_event: notify_donation_event(d, ev)
            )

    return HttpResponse(status=200)


def _nowpayments_handle_ticket_ipn(*, payment_status, order_id, np_id, payment_id):
    notify_event = None
    with transaction.atomic():
        ticket_request = (
            TicketRequest.objects.select_for_update()
            .select_related('team')
            .filter(id=order_id)
            .first()
        )
        if not ticket_request:
            logger.warning(
                'nowpayments_ipn: ticket request not found order_id=%s payment_id=%s status=%s',
                order_id,
                payment_id,
                payment_status,
            )
            return HttpResponse(status=200)

        if payment_status == 'finished':
            result = accept_ticket_request(
                ticket_request,
                nowpayments_id=np_id,
                source='nowpayments_ipn',
            )
            if result.changed:
                notify_event = 'payment.succeeded'
        elif payment_status in ('failed', 'expired'):
            result = reject_ticket_request(ticket_request, source='nowpayments_ipn')
            if result.changed:
                notify_event = 'payment.canceled'
        else:
            logger.info(
                'nowpayments_ipn: ignore status=%s order_id=%s payment_id=%s',
                payment_status,
                order_id,
                payment_id,
            )

    if notify_event:
        from games.telegram.notify import notify_payment_event

        ticket_request = TicketRequest.objects.filter(id=order_id).first()
        if ticket_request is not None:
            transaction.on_commit(lambda tr=ticket_request, ev=notify_event: notify_payment_event(tr, ev))

    return HttpResponse(status=200)


def _resolve_order_id_from_invoice(invoice_id) -> str | None:
    """Map NOWPayments invoice_id to our order_id when the IPN omits order_id."""
    if invoice_id is None or invoice_id == '':
        return None
    key = str(invoice_id)
    from games.donation_service import donation_order_id
    from games.models import Donation

    donation = Donation.objects.filter(nowpayments_id=key).only('id').first()
    if donation is not None:
        return donation_order_id(donation.id)

    ticket = TicketRequest.objects.filter(nowpayments_id=key).only('id').first()
    if ticket is not None:
        return str(ticket.id)
    return None


@csrf_exempt
def nowpayments_ipn(request):
    """
    NOWPayments Instant Payment Notification.

    Routes donation-* order_id to Donation; otherwise TicketRequest by numeric id.
    If order_id is missing, falls back to Donation/TicketRequest.nowpayments_id == invoice_id.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        event_json = json.loads(request.body)
    except Exception:
        return HttpResponse(status=400)

    if not isinstance(event_json, dict):
        return HttpResponse(status=400)

    sig = request.headers.get('x-nowpayments-sig') or request.META.get('HTTP_X_NOWPAYMENTS_SIG')
    try:
        if not verify_ipn_signature(event_json, sig):
            logger.warning('nowpayments_ipn: invalid signature')
            return HttpResponse(status=400)
    except RuntimeError as exc:
        logger.error('nowpayments_ipn: %s', exc)
        return HttpResponse(status=503)

    payment_status = (event_json.get('payment_status') or '').lower()
    raw_order_id = event_json.get('order_id')
    order_id = str(raw_order_id).strip() if raw_order_id is not None else ''
    order_id = order_id or None
    payment_id = event_json.get('payment_id')
    invoice_id = event_json.get('invoice_id')
    np_id = str(invoice_id or payment_id or '') or None

    if not order_id:
        # Invoice→payment widget IPNs often omit order_id; resolve via stored invoice id.
        order_id = _resolve_order_id_from_invoice(invoice_id)
        if not order_id:
            logger.warning(
                'nowpayments_ipn: missing order_id payment_id=%s invoice_id=%s status=%s',
                payment_id,
                invoice_id,
                payment_status,
            )
            return HttpResponse(status=200)

    from games.donation_service import DONATION_ORDER_PREFIX

    if str(order_id).startswith(DONATION_ORDER_PREFIX):
        return _nowpayments_handle_donation_ipn(
            event_json,
            payment_status=payment_status,
            order_id=order_id,
            np_id=np_id,
            payment_id=payment_id,
        )

    return _nowpayments_handle_ticket_ipn(
        payment_status=payment_status,
        order_id=order_id,
        np_id=np_id,
        payment_id=payment_id,
    )


@csrf_exempt
def tribute_webhook(request):
    """
    Tribute Shop webhook.

    Final paid confirmation: name=shop_order, payload.status=paid.
    Failure: name=shop_order_payment_failed.
    Intermediate events are ignored.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    raw_body = request.body
    sig = request.headers.get('trbt-signature') or request.META.get('HTTP_TRBT_SIGNATURE')
    try:
        if not verify_tribute_webhook_signature(raw_body, sig):
            logger.warning('tribute_webhook: invalid signature')
            return HttpResponse(status=400)
    except RuntimeError as exc:
        logger.error('tribute_webhook: %s', exc)
        return HttpResponse(status=503)

    try:
        event_json = json.loads(raw_body)
    except Exception:
        return HttpResponse(status=400)

    if not isinstance(event_json, dict):
        return HttpResponse(status=400)

    event_name = (event_json.get('name') or '').strip()
    payload = event_json.get('payload') or {}
    if not isinstance(payload, dict):
        payload = {}

    if event_name not in ('shop_order', 'shop_order_payment_failed'):
        return HttpResponse(status=200)

    order_uuid = payload.get('uuid')
    notify_event = None
    ticket_request_id = None

    with transaction.atomic():
        ticket_request = _resolve_ticket_request_for_tribute(payload)
        if not ticket_request:
            logger.warning(
                'tribute_webhook: ticket request not found event=%s uuid=%s customerId=%r',
                event_name,
                order_uuid,
                payload.get('customerId'),
            )
            return HttpResponse(status=200)

        ticket_request_id = ticket_request.pk

        if event_name == 'shop_order':
            status = (payload.get('status') or '').lower()
            if status != 'paid':
                logger.info(
                    'tribute_webhook: ignore shop_order status=%s uuid=%s',
                    status,
                    order_uuid,
                )
                return HttpResponse(status=200)
            result = accept_ticket_request(
                ticket_request,
                tribute_id=str(order_uuid) if order_uuid else None,
                source='tribute_webhook',
            )
            if result.changed:
                notify_event = 'payment.succeeded'
        else:
            result = reject_ticket_request(ticket_request, source='tribute_webhook')
            if result.changed:
                notify_event = 'payment.canceled'

    if notify_event and ticket_request_id is not None:
        from games.telegram.notify import notify_payment_event

        ticket_request = TicketRequest.objects.filter(id=ticket_request_id).first()
        if ticket_request is not None:
            transaction.on_commit(
                lambda tr=ticket_request, ev=notify_event: notify_payment_event(tr, ev)
            )

    return HttpResponse(status=200)
