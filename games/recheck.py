from django.shortcuts import get_object_or_404
from games.models import Attempt
from games.views import check_attempt


def recheck(_, attempt_id):
    attempt = get_object_or_404(Attempt, id=attempt_id)
    try:
        check_attempt(attempt)
        attempt.skip = False
        attempt.save()
    except Exception as e:
        print('SKIP Attempt {} while RECHECKING'.format(attempt))
        print('REASON: {}', e)
        attempt.skip = True
        attempt.save()


def recheck_full(_, attempt_id=None, task=None):
    if task is None:
        task = get_object_or_404(Attempt, id=attempt_id).task
    for attempt in Attempt.manager.get_all_task_attempts(
        task=task, exclude_skip=False
    ):
        recheck(None, attempt.id)


def recheck_queue_from_this(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    for attempt in Attempt.manager.get_all_attempts_after_equal(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
    ):
        recheck(None, attempt.id)


def recheck_queue_from_next(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    for attempt in Attempt.manager.get_all_attempts_after(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
    ):
        recheck(None, attempt.id)
