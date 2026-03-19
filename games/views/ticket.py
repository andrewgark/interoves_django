import json
import os
import uuid
from pathlib import Path

from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from games.exception import InvalidFormException
from games.forms import TicketRequestForm
from games.models import TicketRequest
from games.views.util import has_team, redirect_to_referer

from yookassa import Configuration, Payment


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
    ticket_request.status = 'Accepted'
    ticket_request.save()
    return HttpResponse(status=200)


def _configure_yookassa_from_env():
    shop_id = os.environ.get('YOOKASSA_SHOP_ID') or os.environ.get('YOO_KASSA_SHOP_ID')
    secret_key = os.environ.get('YOOKASSA_SECRET_KEY') or os.environ.get('YOO_KASSA_SECRET_KEY')

    if not shop_id:
        try:
            shop_id = Path('secrets/yookassa_shop_id.txt').read_text(encoding='utf-8').strip()
        except Exception:
            shop_id = shop_id or None
    if not secret_key:
        try:
            secret_key = Path('secrets/yookassa_secret_key.txt').read_text(encoding='utf-8').strip()
        except Exception:
            secret_key = secret_key or None

    if not shop_id or not secret_key:
        raise RuntimeError('Missing YooKassa credentials in env: YOOKASSA_SHOP_ID/YOOKASSA_SECRET_KEY')
    Configuration.configure(shop_id, secret_key)


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
        return HttpResponse(status=200)

    try:
        _configure_yookassa_from_env()
        payment = Payment.find_one(payment_id)
        payment_data = dict(payment)
    except Exception:
        # Don't 500 on temporary API failures; YooKassa will retry.
        return HttpResponse(status=200)

    if event == 'payment.waiting_for_capture':
        # We normally create payments with capture=True, but handle this event anyway.
        try:
            Payment.capture(payment_id, {'amount': payment_data.get('amount')})
        except Exception:
            return HttpResponse(status=200)
        return HttpResponse(status=200)

    metadata = payment_data.get('metadata') or {}
    ticket_request_id = metadata.get('ticket_request_id')
    if not ticket_request_id:
        return HttpResponse(status=200)

    with transaction.atomic():
        ticket_request = TicketRequest.objects.select_for_update().filter(id=ticket_request_id).first()
        if not ticket_request:
            return HttpResponse(status=200)

        # Store payment id for debugging / reconciliation
        if not ticket_request.yookassa_id:
            ticket_request.yookassa_id = payment_id

        if event == 'payment.canceled':
            if ticket_request.status == 'Pending':
                ticket_request.status = 'Rejected'
                ticket_request.save()
            return HttpResponse(status=200)

        # payment.succeeded
        if ticket_request.status == 'Accepted':
            return HttpResponse(status=200)

        ticket_request.status = 'Accepted'
        ticket_request.save()
        if ticket_request.team_id:
            team = ticket_request.team
            team.tickets = (team.tickets or 0) + int(ticket_request.tickets or 0)
            team.save(update_fields=['tickets'])

    return HttpResponse(status=200)
