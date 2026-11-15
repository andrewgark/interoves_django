"""Payments hub: one page that links every money-related flow on the site.

Players kept missing donations and the Club subscription because the nav
"Оплата" link led straight to the team-ticket checkout. The hub lives at
/pay/; the ticket checkout moved to /pay/tickets/.
"""
from urllib.parse import urlencode

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_http_methods

from games.club.access.archive import get_club_subscription, has_club_access
from games.club.billing.routes import RUSSIAN_CARD, unit_price_for as ticket_unit_price_for
from games.club.next_game_autumn import PHASE_CLOSED, campaign_phase
from games.identity import has_profile, has_team
from games.models import SavedPaymentMethod, SubscriptionGift, TicketRequest
from games.site.pages import NEW_UI_PROJECT, _project_urls_context

# Provider returns and Telegram linking used to land on /pay/?…; payments
# created before the move still carry those return URLs.
_TICKET_RETURN_PARAMS = ('payment', 'telegram')


def _club_status(user):
    if not has_club_access(user):
        return {'active': False, 'until_label': ''}
    subscription = get_club_subscription(user)
    paid_until = getattr(subscription, 'paid_until', None)
    until_label = ''
    if paid_until and paid_until > timezone.now():
        until_label = date_format(timezone.localtime(paid_until), 'j E')
    return {'active': True, 'until_label': until_label}


def _account_links(user, team):
    """Which "my payments" shortcuts have something behind them for this user."""
    links = {
        'ticket_orders': bool(team) and TicketRequest.objects.filter(team=team).exists(),
        'saved_card': SavedPaymentMethod.objects.filter(
            user=user, provider='yookassa', is_active=True,
            provider_payment_method_id__isnull=False,
        ).exclude(provider_payment_method_id='').exists(),
        'gifts': SubscriptionGift.objects.filter(
            purchaser=user,
            status__in=(SubscriptionGift.STATUS_PAID, SubscriptionGift.STATUS_CLAIMED),
        ).exists(),
    }
    return links if any(links.values()) else {}


@require_http_methods(['GET'])
def pay_hub_page(request):
    if any(key in request.GET for key in _TICKET_RETURN_PARAMS):
        return redirect('{}?{}'.format(reverse('new_pay'), urlencode(request.GET, doseq=True)))

    team = None
    club = {'active': False, 'until_label': ''}
    account_links = {}
    if request.user.is_authenticated and has_profile(request.user):
        if has_team(request.user):
            team = request.user.profile.team_on
        club = _club_status(request.user)
        account_links = _account_links(request.user, team)
    ticket_price = ticket_unit_price_for(team, RUSSIAN_CARD)
    return render(request, 'site/pay_hub.html', {
        'team': team,
        'team_tickets': team.tickets if team else 0,
        'club': club,
        'ticket_price_display': '{:,}'.format(ticket_price).replace(',', ' '),
        'account_links': account_links,
        'show_next_game_vote': campaign_phase() != PHASE_CLOSED,
        'page_title': 'Оплата и поддержка',
        **_project_urls_context(NEW_UI_PROJECT),
    })
