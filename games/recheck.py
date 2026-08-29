from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from games.models import Attempt, ChainTaskState, CHAIN_TASK_TYPES, GameTaskGroup
from games.util import better_status
from games.views.views import check_attempt
from games.views.track import track_actor_task_change, track_attempt_change


def recheck(_, attempt_id, *, notify=True):
    attempt = get_object_or_404(Attempt, id=attempt_id)
    try:
        check_attempt(attempt, preserve_achievement=True)
        attempt.skip = False
        attempt.save()
    except Exception as e:
        print('SKIP Attempt {} while RECHECKING'.format(attempt))
        print('REASON: {}'.format(e))
        attempt.skip = True
        attempt.save()
    if notify:
        track_attempt_change(attempt, reason='attempt.rechecked')
    return attempt


def _recheck_many(attempts, *, reason):
    changed = {}
    for source in list(attempts):
        attempt = recheck(None, source.id, notify=False)
        key = (
            attempt.task_id,
            attempt.game_id,
            attempt.team_id,
            attempt.user_id,
            attempt.anon_key,
        )
        changed[key] = attempt
    for attempt in changed.values():
        track_attempt_change(attempt, reason=reason)
    return list(changed.values())


def recheck_full(_, attempt_id=None, task=None):
    if task is None:
        task = get_object_or_404(Attempt, id=attempt_id).task
    return _recheck_many(
        Attempt.manager.get_all_task_attempts(task=task, exclude_skip=False),
        reason='task.rechecked_full',
    )


def recheck_queue_from_this(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    return _recheck_many(Attempt.manager.get_all_attempts_after_equal(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
        user=this_attempt.user if this_attempt.user_id else None,
        anon_key=this_attempt.anon_key,
        game=this_attempt.game,
    ), reason='task.rechecked_from_attempt')


def recheck_queue_from_next(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    return _recheck_many(Attempt.manager.get_all_attempts_after(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
        user=this_attempt.user if this_attempt.user_id else None,
        anon_key=this_attempt.anon_key,
        game=this_attempt.game,
    ), reason='task.rechecked_after_attempt')


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
        recheck_chain_task(
            task=task, team=team, user=user, anon_key=anon_key, game=this_attempt.game,
        )
        return

    attempts = Attempt.manager.get_all_attempts(
        team, task, exclude_skip=False, user=user, anon_key=anon_key,
    )
    return _recheck_many(attempts, reason='task.rechecked_chronological')


def recheck_chain_task(
    task, team=None, user=None, anon_key=None, game=None, *,
    notify=True, protect_existing_points=False,
):
    """
    Optimised full replay for cumulative chain tasks.

    Replays ALL attempts for one actor+task pair in a single transaction:
    - One DB read for all attempts.
    - State carried in memory between attempts; no per-attempt DB round-trip.
    - Both game_mode buckets (general / tournament) are rebuilt in one pass.
    - ChainTaskState rows are locked at the start so concurrent submissions
      are blocked until recheck completes.
    - Each Attempt.state is updated in the DB as the audit trail.
    """
    from games.check import CheckerFactory

    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise ValueError('recheck_chain_task: pass game= for tasks in multiple games')

    with transaction.atomic():
        # Lock (and create if missing) both possible ChainTaskState rows upfront.
        for mode in ('general', 'tournament'):
            ChainTaskState.objects.get_or_create(
                team=team, user=user, anon_key=anon_key,
                task=task, game=game, game_mode=mode,
                defaults={'state': None},
            )
        locked_rows = {
            row.game_mode: row
            for row in ChainTaskState.objects.select_for_update().filter(
                team=team, user=user, anon_key=anon_key, task=task, game=game,
            )
        }
        checker_type = task.get_checker()
        checker_data = task.checker_data or ''

        # current in-memory chain state per game_mode
        states = {'general': None, 'tournament': None}

        attempts = Attempt.manager.get_all_attempts(
            team, task, exclude_skip=False, user=user, anon_key=anon_key, game=game,
        )

        attempts_by_mode = {'general': [], 'tournament': []}
        for attempt in attempts:
            attempts_by_mode[game.get_current_mode(attempt)].append(attempt)

        # Snapshot achievements before mutating a single row.  Salad normally
        # scores its latest state (hints can reduce it), so it needs an explicit
        # edit-time floor.  Other task types are protected by monotonic
        # Attempt.points below.
        salad_floors = {'general': None, 'tournament': None}
        if task.task_type == 'word_salad' and protect_existing_points:
            from games.word_salad import result_points_from_attempts
            for mode, mode_attempts in attempts_by_mode.items():
                salad_floors[mode] = result_points_from_attempts(mode_attempts)

        for mode, row in locked_rows.items():
            mode_attempts = attempts_by_mode.get(mode, [])
            if row.completed_at is None:
                completed_attempts = [a for a in mode_attempts if a.status == 'Ok']
                if completed_attempts:
                    first = min(completed_attempts, key=lambda a: a.time or timezone.now())
                    row.completed_at = first.time or timezone.now()
                    row.completed_revision = first.task_revision or task.attempt_revision
            row.state = None
            row.last_attempt = None

        for attempt in attempts:
            mode = game.get_current_mode(attempt)
            last_state = states[mode]
            previous_status = attempt.status
            previous_points = attempt.points
            try:
                from games.models import CheckerType as CT
                if task.task_type == 'replacements_lines':
                    ct = CT.objects.get(id='replacements_lines')
                elif task.task_type == 'raddle':
                    ct = CT.objects.get(id='raddle')
                else:
                    ct = checker_type
                checker = CheckerFactory().create_checker(ct, checker_data, last_state)
                result = checker.check(attempt.text, attempt)
                from decimal import Decimal
                current_points = Decimal(str(result.points or 0))
                if task.task_type != 'word_salad':
                    current_points *= task.get_points()
                attempt.current_status = result.status
                attempt.current_points = current_points
                attempt.checked_revision = task.attempt_revision
                attempt.status = (
                    previous_status
                    if previous_status and better_status(previous_status, result.status)
                    else result.status
                )
                attempt.points = max(
                    Decimal(str(previous_points or 0)),
                    current_points,
                )
                attempt.state = result.state
                attempt.comment = result.comment
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
                    if (
                        locked_rows[mode].completed_at is None
                        and (
                            attempt.current_status == 'Ok'
                            or _state_is_complete(task, attempt.state)
                        )
                    ):
                        locked_rows[mode].completed_at = attempt.time or timezone.now()
                        locked_rows[mode].completed_revision = task.attempt_revision

        if task.task_type == 'word_salad' and protect_existing_points:
            from decimal import Decimal
            for mode, floor in salad_floors.items():
                mode_attempts = attempts_by_mode.get(mode, [])
                if floor is None or not mode_attempts:
                    continue
                latest = mode_attempts[-1]
                latest.recheck_points_floor = max(
                    Decimal(str(latest.recheck_points_floor or 0)),
                    Decimal(str(floor or 0)),
                )
                latest.save(update_fields=['recheck_points_floor'])

        # Persist updated ChainTaskState rows.
        for row in locked_rows.values():
            row.validated_revision = task.attempt_revision
            row.save(update_fields=[
                'state', 'last_attempt', 'completed_at', 'completed_revision',
                'validated_revision', 'updated_at',
            ])

        # Product completion records are idempotent (unique per actor/game
        # instance).  A formerly wrong historical submission may complete a
        # personal daily game during replay, so backfill that dependent result.
        if (user is not None or anon_key) and any(
            row.completed_at is not None for row in locked_rows.values()
        ):
            from games.analytics import register_completed_game
            register_completed_game(
                user=user,
                anon_key=anon_key,
                task=task,
                game=game,
            )

    if notify:
        track_actor_task_change(
            task,
            team=team,
            user=user,
            anon_key=anon_key,
            game=game,
            reason='task.chain_rechecked',
        )


def _state_is_complete(task, state):
    from games.analytics import is_task_completion_state
    try:
        return is_task_completion_state(task, state)
    except Exception:
        return False


def recheck_task_after_edit(
    task, *, previous_revision=None, previous_max_points=None,
    changed_fields=None,
):
    """Replay every actor affected by one validation-affecting Task edit.

    Task.save calls this while holding the Task row lock and inside the same DB
    transaction as the content change.  Consequently readers see either the
    complete old revision or the complete new revision, never a partial replay.
    """
    attempts = list(
        Attempt.manager.filter(task=task)
        .exclude(skip=True)
        .values('team_id', 'user_id', 'anon_key', 'game_id')
        .distinct()
    )
    if not attempts:
        return 0

    count = 0
    for actor in attempts:
        team_id = actor['team_id']
        user_id = actor['user_id']
        anon_key = actor['anon_key']
        game_id = actor['game_id']
        game = None
        if game_id:
            from games.models import Game
            game = Game.objects.get(pk=game_id)
        else:
            game = GameTaskGroup.resolve_game_for_task(task)
        if game is None:
            continue

        team = team_id and _team(team_id)
        user = user_id and _user(user_id)
        actor_attempts = Attempt.manager.get_all_attempts(
            team,
            task,
            exclude_skip=False,
            user=user,
            anon_key=anon_key,
            game=game,
        )
        attempts_info = Attempt.manager.get_attempts_info(
            team,
            task,
            user=user,
            anon_key=anon_key,
            game=game,
        )
        _preserve_legacy_points_completion(
            task,
            actor_attempts,
            previous_max_points=previous_max_points,
            previous_result_points=attempts_info.get_result_points(),
            result_attempt=attempts_info.get_result_attempt(),
        )

        if task.task_type in CHAIN_TASK_TYPES:
            recheck_chain_task(
                task,
                team,
                user,
                anon_key,
                game,
                notify=False,
                protect_existing_points=True,
            )
        else:
            _recheck_many(actor_attempts, reason='task.edited_recheck')
        count += 1
    return count


def _team(pk):
    from games.models import Team
    return Team.objects.get(pk=pk)


def _user(pk):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.get(pk=pk)


def _preserve_legacy_points_completion(
    task, attempts, *, previous_max_points, previous_result_points,
    result_attempt,
):
    """Materialise legacy ``points == old max`` completion before max changes."""
    from decimal import Decimal, InvalidOperation

    attempts = list(attempts or [])
    if not attempts or any(attempt.status == 'Ok' for attempt in attempts):
        return
    try:
        old_max = Decimal(str(previous_max_points))
    except (InvalidOperation, TypeError, ValueError):
        return
    if old_max <= 0:
        return

    try:
        result_points = Decimal(str(previous_result_points or 0))
    except (InvalidOperation, TypeError, ValueError):
        return
    if result_attempt is None:
        result_attempt = max(
            attempts,
            key=lambda attempt: Decimal(str(attempt.points or 0)),
        )
    if result_points < old_max:
        return
    result_attempt.status = 'Ok'
    result_attempt.save(update_fields=['status'])
