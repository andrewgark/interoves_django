from django.http import JsonResponse
from django.contrib.auth.decorators import user_passes_test
from games.exception import *
from games.forms import TicketRequestForm
from games.views.util import redirect_to_referer, has_team


@user_passes_test(has_team)
def request_ticket(request):
    team = request.user.profile.team_on
    form = TicketRequestForm(request.POST)
    if not form.is_valid():
        raise InvalidFormException('Введите число билетов от 1 до 20')
    ticket_request = form.save(commit=True)
    ticket_request.team = team
    ticket_request.save()
    return redirect_to_referer(request)
