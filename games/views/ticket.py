import requests
import json
import multiprocessing
import time

from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import user_passes_test
from games.exception import *
from games.forms import TicketRequestForm
from games.models import TicketRequest, PendingTicketRequest
from django.shortcuts import get_object_or_404
from games.views.util import redirect_to_referer, has_team
from yookassa import Configuration, Payment


@user_passes_test(has_team)
def request_ticket(request):
    team = request.user.profile.team_on
    form = TicketRequestForm(request.POST)
    if not form.is_valid():
        raise InvalidFormException('Введите число билетов от 1 до 20')
    ticket_request = form.save(commit=True)
    ticket_request.team = team
    ticket_request.save()
    p = multiprocessing.Process(target=start_checking)
    p.start()
    return redirect_to_referer(request)


def check_order(request):
    event_json = json.loads(request.body)
    print(event_json)
    if event_json['event'] != "payment.succeeded":
        return HttpResponse(status=200)
    ticket_request = get_object_or_404(TicketRequest, yookassa_id=event_json['description'])
    ticket_request.status = 'Accepted'
    ticket_request.save()
    return HttpResponse(status=200)


YOOKASSA_SHOP_ID = '830482'
YOOKASSA_TOKEN = 'live_e_FcKEkvDpoMP_D7bOyO1LyaWFBw8W5NtO2htz6W7gw'


def start_checking():
    for i in range(20):
        check_yoomoney()
        time.sleep(30)


def check_yoomoney():
    print('task!')
    pending_ticket_requests = list(TicketRequest.objects.filter(status='Pending'))
    if len(pending_ticket_requests) == 0:
        return
    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_TOKEN)

    payments = dict(Payment.list(params={
        'status': 'succeeded'
    }))['items']

    succeeded_descs = {
        x.get('description') for x in payments
    }
    print(succeeded_descs)
    for ticket_request in pending_ticket_requests:
        print(ticket_request, ticket_request.yookassa_id)
        if ticket_request.yookassa_id is not None and \
           ticket_request.yookassa_id in succeeded_descs:
            ticket_request.status = 'Accepted'
            ticket_request.save()
            print(ticket_request.yookassa_id, 'is now Accepted')
