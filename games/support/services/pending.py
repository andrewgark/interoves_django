from dataclasses import dataclass
from typing import List, Optional

from django.urls import reverse

from games.support.services.feed import (
    actor_label_for_attempt,
    actor_url_for_attempt,
    attempt_correct_answer,
    attempt_submission_text,
    preview_text,
)


@dataclass(frozen=True)
class PendingItem:
    time: object
    kind: str
    object_id: int
    title: str
    detail: str
    actor_label: str
    actor_url: str
    game_id: Optional[str]
    game_url: Optional[str]
    admin_url: str
    actions: tuple
    submission_text: Optional[str] = None
    correct_answer: Optional[str] = None


def _game_url(game_id: Optional[str]) -> Optional[str]:
    if not game_id:
        return None
    return reverse('support:game', kwargs={'game_id': game_id})


def get_pending_queue(*, limit: int = 100) -> List[PendingItem]:
    from games.models import Attempt, BugReport, TicketRequest

    items: List[PendingItem] = []

    attempts = (
        Attempt.manager.filter(status='Pending')
        .select_related('team', 'user', 'user__profile', 'task', 'task__task_group', 'game')
        .order_by('-time')[:limit]
    )
    for attempt in attempts:
        task = attempt.task
        items.append(PendingItem(
            time=attempt.time,
            kind='attempt',
            object_id=attempt.pk,
            title='Посылка #{}'.format(attempt.pk),
            detail='{} · {}'.format(
                attempt.game_id or '—',
                task.number if task else '—',
            ),
            submission_text=attempt_submission_text(attempt),
            correct_answer=attempt_correct_answer(attempt),
            actor_label=actor_label_for_attempt(attempt),
            actor_url=actor_url_for_attempt(attempt),
            game_id=attempt.game_id,
            game_url=_game_url(attempt.game_id),
            admin_url=reverse('admin:games_attempt_change', args=[attempt.pk]),
            actions=('recheck', 'set_ok', 'confirm_prestatus'),
        ))

    tickets = (
        TicketRequest.objects.filter(status='Pending')
        .select_related('team')
        .order_by('-time')[:limit]
    )
    for ticket in tickets:
        team = ticket.team
        actor_label = (team.visible_name or team.name) if team else '—'
        actor_url = reverse('support:actor_team', kwargs={'team_name': team.name}) if team else reverse('support:hub')
        items.append(PendingItem(
            time=ticket.time,
            kind='ticket',
            object_id=ticket.pk,
            title='Билет #{}'.format(ticket.pk),
            detail='{} ₽ · {} билет(ов)'.format(ticket.money, ticket.tickets),
            actor_label=actor_label,
            actor_url=actor_url,
            game_id=None,
            game_url=None,
            admin_url=reverse('admin:games_ticketrequest_change', args=[ticket.pk]),
            actions=('ticket_accept', 'ticket_reject'),
        ))

    bugs = (
        BugReport.objects.filter(status='Pending')
        .select_related('task', 'game', 'team', 'user', 'user__profile')
        .order_by('-time')[:limit]
    )
    for bug in bugs:
        if bug.team_id:
            actor_label = bug.team.visible_name or bug.team.name
            actor_url = reverse('support:actor_team', kwargs={'team_name': bug.team_id})
        elif bug.user_id:
            profile = getattr(bug.user, 'profile', None)
            if profile:
                actor_label = '{} {}'.format(profile.first_name, profile.last_name).strip()
            else:
                actor_label = bug.user.username
            actor_url = reverse('support:actor_user', kwargs={'user_id': bug.user_id})
        elif bug.anon_key:
            tail = bug.anon_key[-8:] if len(bug.anon_key) >= 8 else bug.anon_key
            actor_label = 'Аноним ··{}'.format(tail)
            actor_url = reverse('support:actor_anon', kwargs={'anon_key': bug.anon_key})
        else:
            actor_label = '—'
            actor_url = reverse('support:hub')
        items.append(PendingItem(
            time=bug.time,
            kind='bug',
            object_id=bug.pk,
            title='Баг #{}'.format(bug.pk),
            detail='{} · task {} · {}'.format(
                bug.game_id,
                bug.task.number if bug.task else '—',
                preview_text(bug.text, max_len=80),
            ),
            actor_label=actor_label,
            actor_url=actor_url,
            game_id=bug.game_id,
            game_url=_game_url(bug.game_id),
            admin_url=reverse('admin:games_bugreport_change', args=[bug.pk]),
            actions=('bug_reviewed', 'bug_dismissed'),
        ))

    items.sort(key=lambda row: row.time or '', reverse=True)
    return items[:limit]
