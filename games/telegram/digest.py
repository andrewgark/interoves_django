from datetime import timedelta

from django.utils import timezone

from games.models import BugReport, CorporateGameOrder, Registration, TicketRequest
from games.telegram.notify import _escape, _join_lines


def build_daily_digest(since=None) -> str:
    since = since or (timezone.now() - timedelta(days=1))

    registrations = Registration.objects.filter(time__gte=since).count()
    tickets = TicketRequest.objects.filter(time__gte=since)
    tickets_pending = tickets.filter(status='Pending').count()
    tickets_accepted = tickets.filter(status='Accepted').count()
    bugs = BugReport.objects.filter(time__gte=since)
    bugs_pending = bugs.filter(status='Pending').count()
    orders = CorporateGameOrder.objects.filter(created_at__gte=since).count()

    return _join_lines([
        '<b>Дайджест за 24 часа</b>',
        '',
        'Регистрации: {}'.format(registrations),
        'Билеты: {} pending · {} accepted'.format(tickets_pending, tickets_accepted),
        'Баг-репорты: {} ({} pending)'.format(bugs.count(), bugs_pending),
        'Корпоративные заявки: {}'.format(orders),
        '',
        'Сгенерировано: {}'.format(timezone.localtime(timezone.now()).strftime('%d.%m.%Y %H:%M')),
    ])
