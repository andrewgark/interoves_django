"""Shared attempt/ticket/bug ops used by Django admin and Support Console."""
from django.db import transaction

from games.models import Attempt, Hint, HintAttempt, TicketRequest
from games.recheck import recheck
from games.views.track import track_attempt_change
from games.ticket_service import (
    accept_ticket_request as accept_ticket_request_record,
    reject_ticket_request as reject_ticket_request_record,
)


def set_attempt_ok(attempt: Attempt) -> None:
    try:
        if attempt.task and attempt.task.task_type in ('wall', 'replacements_lines', 'raddle'):
            attempt.points = attempt.task.get_results_max_points()
        else:
            attempt.points = attempt.get_max_points()
    except Exception:
        attempt.points = attempt.get_max_points()
    attempt.status = 'Ok'
    if attempt.task and attempt.task.task_type == 'autohint':
        hints = set(Hint.objects.filter(task=attempt.task))
        hint_attempts = HintAttempt.objects.filter(team=attempt.team, hint__in=hints)
        hint_attempts = sorted(hint_attempts, key=lambda h: h.time, reverse=True)
        if hint_attempts:
            last_hint_attempt = hint_attempts[0]
            last_hint_attempt.is_real_request = False
            last_hint_attempt.save()
    attempt.save()
    track_attempt_change(attempt, reason='attempt.set_ok')


def confirm_attempt_prestatus(attempt: Attempt) -> None:
    attempt.status = attempt.possible_status
    attempt.save()
    track_attempt_change(attempt, reason='attempt.prestatus_confirmed')


def run_recheck(attempt_id: int) -> None:
    recheck(None, attempt_id)


def accept_ticket(ticket_id: int, *, source: str = 'support') -> None:
    with transaction.atomic():
        locked = TicketRequest.objects.select_for_update().select_related('team').get(pk=ticket_id)
        accept_ticket_request_record(locked, source=source)


def reject_ticket(ticket_id: int, *, source: str = 'support') -> None:
    with transaction.atomic():
        locked = TicketRequest.objects.select_for_update().get(pk=ticket_id)
        reject_ticket_request_record(locked, source=source)
