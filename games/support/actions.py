from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from games.models import Attempt, BugReport, TicketRequest
from games.ops_actions import (
    accept_ticket,
    confirm_attempt_prestatus,
    reject_ticket,
    run_recheck,
    set_attempt_ok,
)
from games.recheck import recheck_chain_task
from games.support.access import support_console_required
from games.support.services.chain import is_chain_task

ATTEMPT_ACTIONS = frozenset({'recheck', 'set_ok', 'confirm_prestatus', 'chain_replay'})
TICKET_ACTIONS = frozenset({'ticket_accept', 'ticket_reject'})
BUG_ACTIONS = frozenset({'bug_reviewed', 'bug_dismissed'})


def _safe_next_url(request, default='/support/pending/'):
    next_url = (request.POST.get('next') or default).strip()
    if next_url.startswith('/') and not next_url.startswith('//'):
        return next_url
    return default


@support_console_required
@require_POST
def perform_action(request):
    kind = (request.POST.get('kind') or '').strip()
    action = (request.POST.get('action') or '').strip()
    raw_id = request.POST.get('id')
    next_url = _safe_next_url(request)

    try:
        obj_id = int(raw_id)
    except (TypeError, ValueError):
        messages.error(request, 'Некорректный id.')
        return redirect(next_url)

    try:
        if kind == 'attempt' and action in ATTEMPT_ACTIONS:
            _perform_attempt_action(obj_id, action)
            messages.success(request, 'Посылка #{}: {}'.format(obj_id, action))
        elif kind == 'ticket' and action in TICKET_ACTIONS:
            _perform_ticket_action(obj_id, action)
            messages.success(request, 'Билет #{}: {}'.format(obj_id, action))
        elif kind == 'bug' and action in BUG_ACTIONS:
            _perform_bug_action(obj_id, action)
            messages.success(request, 'Баг #{}: {}'.format(obj_id, action))
        else:
            raise PermissionDenied('Неизвестное действие')
    except (Attempt.DoesNotExist, TicketRequest.DoesNotExist, BugReport.DoesNotExist):
        messages.error(request, 'Объект не найден.')
    except PermissionDenied as exc:
        messages.error(request, str(exc) or 'Действие запрещено.')
    except Exception as exc:
        messages.error(request, 'Ошибка: {}'.format(exc))

    return redirect(next_url)


def _perform_attempt_action(attempt_id: int, action: str) -> None:
    attempt = Attempt.manager.select_related('task', 'team').filter(pk=attempt_id).first()
    if attempt is None:
        raise Attempt.DoesNotExist
    if action == 'recheck':
        run_recheck(attempt_id)
        return
    if action == 'set_ok':
        set_attempt_ok(attempt)
        return
    if action == 'confirm_prestatus':
        if not attempt.possible_status:
            raise PermissionDenied('Нет possible_status')
        confirm_attempt_prestatus(attempt)
        return
    if action == 'chain_replay':
        if not is_chain_task(attempt):
            raise PermissionDenied('Не chain-задание')
        recheck_chain_task(
            task=attempt.task,
            team=attempt.team,
            user=attempt.user if attempt.user_id else None,
            anon_key=attempt.anon_key,
            game=attempt.game,
        )
        return
    raise PermissionDenied('Неизвестное действие')


def _perform_ticket_action(ticket_id: int, action: str) -> None:
    ticket = TicketRequest.objects.filter(pk=ticket_id, status='Pending').first()
    if ticket is None:
        raise TicketRequest.DoesNotExist
    if action == 'ticket_accept':
        accept_ticket(ticket_id, source='support')
        return
    if action == 'ticket_reject':
        reject_ticket(ticket_id, source='support')
        return
    raise PermissionDenied('Неизвестное действие')


def _perform_bug_action(bug_id: int, action: str) -> None:
    bug = BugReport.objects.filter(pk=bug_id, status='Pending').first()
    if bug is None:
        raise BugReport.DoesNotExist
    if action == 'bug_reviewed':
        bug.status = 'Reviewed'
        bug.save(update_fields=['status'])
        return
    if action == 'bug_dismissed':
        bug.status = 'Dismissed'
        bug.save(update_fields=['status'])
        return
    raise PermissionDenied('Неизвестное действие')
