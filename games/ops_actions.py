"""Shared attempt/ticket/bug ops used by Django admin and Support Console."""
import json

from django.db import transaction

from games.models import Attempt, CheckerType, GameTaskGroup, Hint, HintAttempt, Task, TicketRequest
from games.recheck import recheck
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


def confirm_attempt_prestatus(attempt: Attempt) -> None:
    attempt.status = attempt.possible_status
    attempt.save()


def add_attempt_to_checker(attempt: Attempt) -> None:
    if not attempt.task:
        return
    if attempt.task.task_type != 'wall':
        attempt.task.checker_data = attempt.task.checker_data + '\n' + attempt.text
        attempt.task.save()
        return
    json_data = json.loads(attempt.task.checker_data)
    json_attempt = json.loads(attempt.text)
    for category in json_data['answers']:
        if sorted([x.lower() for x in category['words']]) == sorted([x.lower() for x in json_attempt['words']]):
            category['checker'] = category['checker'] + '\n' + json_attempt['explanation']
    attempt.task.checker_data = json.dumps(json_data)
    attempt.task.save()


def run_recheck(attempt_id: int) -> None:
    recheck(None, attempt_id)


def run_recheck_after_add_to_checker(attempt_id: int) -> None:
    attempt = Attempt.manager.select_related('task').filter(pk=attempt_id).first()
    if attempt is None:
        return
    add_attempt_to_checker(attempt)
    run_recheck(attempt_id)


def accept_ticket(ticket_id: int, *, source: str = 'support') -> None:
    with transaction.atomic():
        locked = TicketRequest.objects.select_for_update().select_related('team').get(pk=ticket_id)
        accept_ticket_request_record(locked, source=source)


def reject_ticket(ticket_id: int, *, source: str = 'support') -> None:
    with transaction.atomic():
        locked = TicketRequest.objects.select_for_update().get(pk=ticket_id)
        reject_ticket_request_record(locked, source=source)


def set_ok_and_create_new_task(attempt: Attempt) -> None:
    """Game 49 helper — kept for admin parity."""
    attempt.status = 'Ok'
    attempt.save()
    g = attempt.game or GameTaskGroup.resolve_game_for_task(attempt.task)
    team_number = attempt.team.get_team_reg_number(g)
    if team_number is None:
        return
    task = attempt.task
    task_data = json.loads(task.text)
    task_checker_data = json.loads(task.checker_data)
    try:
        max_number = max([
            int(x.number.split('.')[1])
            for x in Task.objects.filter(task_group=task.task_group)
            if x.number.startswith('2.')
        ])
    except Exception:
        max_number = 0
    new_task = Task(
        number='2.{}'.format(max_number + 1),
        task_group=task.task_group,
        tags={'team': attempt.team.name, 'task': task_checker_data['tag']},
        text='{}<br><br><b>{}</b>'.format(task_checker_data.get('tag_text'), attempt.text),
        checker_data=task_data['list'][team_number],
        answer=task_data['list'][team_number],
        answer_comment='',
        task_type='with_tag',
        checker=CheckerType('equals_with_possible_spaces'),
        points=1,
    )
    new_task.save()
