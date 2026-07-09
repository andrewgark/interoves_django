"""Shared ticket request accept/reject and stuck-payment helpers."""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from games.models import TicketRequest

logger = logging.getLogger(__name__)

STUCK_TICKET_REQUEST_MINUTES = 30


@dataclass(frozen=True)
class TicketAcceptResult:
    changed: bool
    credited: bool
    tickets_credited: int
    already_accepted: bool
    no_team: bool


@dataclass(frozen=True)
class TicketRejectResult:
    changed: bool
    already_final: bool


def accept_ticket_request(ticket_request, *, yookassa_id=None, source='unknown') -> TicketAcceptResult:
    """
    Idempotently mark a ticket request Accepted and credit team.tickets once.

    Caller should use select_for_update() when handling concurrent updates (webhook, admin).
    """
    already_accepted = ticket_request.status == 'Accepted'
    update_fields = []

    if yookassa_id and not ticket_request.yookassa_id:
        ticket_request.yookassa_id = yookassa_id
        update_fields.append('yookassa_id')

    if already_accepted:
        if update_fields:
            ticket_request.save(update_fields=update_fields)
        return TicketAcceptResult(
            changed=False,
            credited=False,
            tickets_credited=0,
            already_accepted=True,
            no_team=not ticket_request.team_id,
        )

    ticket_request.status = 'Accepted'
    update_fields.append('status')
    ticket_request.save(update_fields=update_fields)

    if not ticket_request.team_id:
        logger.warning(
            'accept_ticket_request: accepted without team ticket_request_id=%s source=%s',
            ticket_request.pk,
            source,
        )
        return TicketAcceptResult(
            changed=True,
            credited=False,
            tickets_credited=0,
            already_accepted=False,
            no_team=True,
        )

    team = ticket_request.team
    tickets_credited = int(ticket_request.tickets or 0)
    team.tickets = (team.tickets or 0) + tickets_credited
    team.save(update_fields=['tickets'])
    logger.info(
        'accept_ticket_request: ticket_request_id=%s team_id=%s tickets_credited=%s source=%s',
        ticket_request.pk,
        ticket_request.team_id,
        tickets_credited,
        source,
    )
    return TicketAcceptResult(
        changed=True,
        credited=True,
        tickets_credited=tickets_credited,
        already_accepted=False,
        no_team=False,
    )


def reject_ticket_request(ticket_request, *, source='unknown') -> TicketRejectResult:
    """Reject a pending ticket request; no-op if already Accepted/Rejected."""
    if ticket_request.status != 'Pending':
        return TicketRejectResult(changed=False, already_final=True)

    ticket_request.status = 'Rejected'
    ticket_request.save(update_fields=['status'])
    logger.info(
        'reject_ticket_request: ticket_request_id=%s source=%s',
        ticket_request.pk,
        source,
    )
    return TicketRejectResult(changed=True, already_final=False)


def stuck_pending_ticket_requests(*, minutes=None):
    """
    Pending requests with a YooKassa payment id older than the threshold.

    These are likely paid but the webhook has not confirmed them yet.
    """
    threshold = minutes if minutes is not None else STUCK_TICKET_REQUEST_MINUTES
    cutoff = timezone.now() - timedelta(minutes=threshold)
    return (
        TicketRequest.objects.filter(
            status='Pending',
            yookassa_id__isnull=False,
            time__lt=cutoff,
        )
        .exclude(yookassa_id='')
        .select_related('team')
        .order_by('time')
    )


def stuck_pending_ticket_count(*, minutes=None) -> int:
    return stuck_pending_ticket_requests(minutes=minutes).count()


def build_stuck_tickets_alert(*, minutes=None) -> str | None:
    """Telegram alert body for stuck pending ticket requests, or None if none."""
    threshold = minutes if minutes is not None else STUCK_TICKET_REQUEST_MINUTES
    qs = stuck_pending_ticket_requests(minutes=threshold)
    total = qs.count()
    if total == 0:
        return None

    lines = [
        '<b>⚠️ Зависшие заявки на билеты</b>',
        'Pending с yookassa_id старше {} мин: {}'.format(threshold, total),
        '',
    ]
    for ticket in qs[:10]:
        team = getattr(ticket.team, 'visible_name', None) or ticket.team_id or '—'
        lines.append(
            '#{} · {} · {} ₽ · {}'.format(
                ticket.pk,
                team,
                ticket.money,
                ticket.time.strftime('%d.%m %H:%M'),
            )
        )
    if total > 10:
        lines.append('… и ещё {}'.format(total - 10))
    lines.append('')
    lines.append('Проверьте webhook YooKassa и логи accept_ticket_request.')
    return '\n'.join(lines)
