from django.db import transaction
from django.shortcuts import get_object_or_404
from games.models import Attempt, ChainTaskState, CHAIN_TASK_TYPES
from games.views.views import check_attempt


def recheck(_, attempt_id):
    attempt = get_object_or_404(Attempt, id=attempt_id)
    try:
        check_attempt(attempt)
        attempt.skip = False
        attempt.save()
    except Exception as e:
        print('SKIP Attempt {} while RECHECKING'.format(attempt))
        print('REASON: {}'.format(e))
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


def recheck_team_task_all_chronological(_, attempt_id):
    """
    Перепроверить все посылки того же актора (команда / личный / аноним), что и у выбранной,
    по тому же заданию, в хронологическом порядке.

    For chain tasks (wall, replacements_lines) delegates to recheck_chain_task which
    replays the whole chain in a single transaction in O(N) without per-attempt DB reads.
    """
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    task = this_attempt.task
    team = this_attempt.team
    user = this_attempt.user if this_attempt.user_id else None
    anon_key = this_attempt.anon_key

    if task.task_type in CHAIN_TASK_TYPES:
        recheck_chain_task(task=task, team=team, user=user, anon_key=anon_key)
        return

    attempts = Attempt.manager.get_all_attempts(
        team, task, exclude_skip=False, user=user, anon_key=anon_key,
    )
    for attempt in attempts:
        recheck(None, attempt.id)


def recheck_chain_task(task, team=None, user=None, anon_key=None):
    """
    Optimised full recheck for wall / replacements_lines.

    Replays ALL attempts for one actor+task pair in a single transaction:
    - One DB read for all attempts.
    - State carried in memory between attempts; no per-attempt DB round-trip.
    - Both game_mode buckets (general / tournament) are rebuilt in one pass.
    - ChainTaskState rows are locked at the start so concurrent submissions
      are blocked until recheck completes.
    - Each Attempt.state is updated in the DB as the audit trail.
    """
    from games.check import CheckerFactory

    game = task.task_group.game

    with transaction.atomic():
        # Lock (and create if missing) both possible ChainTaskState rows upfront.
        for mode in ('general', 'tournament'):
            ChainTaskState.objects.get_or_create(
                team=team, user=user, anon_key=anon_key,
                task=task, game_mode=mode,
                defaults={'state': None},
            )
        locked_rows = {
            row.game_mode: row
            for row in ChainTaskState.objects.select_for_update().filter(
                team=team, user=user, anon_key=anon_key, task=task,
            )
        }
        # Reset both chains.
        for row in locked_rows.values():
            row.state = None
            row.last_attempt = None

        checker_type = task.get_checker()
        checker_data = task.checker_data or ''

        # current in-memory chain state per game_mode
        states = {'general': None, 'tournament': None}

        attempts = Attempt.manager.get_all_attempts(
            team, task, exclude_skip=False, user=user, anon_key=anon_key,
        )

        for attempt in attempts:
            mode = game.get_current_mode(attempt)
            last_state = states[mode]
            try:
                from games.models import CheckerType as CT
                if task.task_type == 'replacements_lines':
                    ct = CT.objects.get(id='replacements_lines')
                else:
                    ct = checker_type
                checker = CheckerFactory().create_checker(ct, checker_data, last_state)
                result = checker.check(attempt.text, attempt)
                attempt.status = result.status
                attempt.points = result.points * task.get_points()
                attempt.state = result.state
                attempt.skip = False
            except Exception as e:
                print('SKIP Attempt {} while RECHECKING chain'.format(attempt))
                print('REASON: {}'.format(e))
                attempt.skip = True
                attempt.state = last_state  # preserve previous state so chain continues
            attempt.save()

            if not attempt.skip:
                states[mode] = attempt.state
                if mode in locked_rows:
                    locked_rows[mode].state = attempt.state
                    locked_rows[mode].last_attempt = attempt

        # Persist updated ChainTaskState rows.
        for row in locked_rows.values():
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])
