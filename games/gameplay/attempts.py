"""Check one attempt and persist it with the state of its task."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.gameplay.client_events import receipt_attempt
from games.models import (
    CHAIN_TASK_TYPES,
    Attempt,
    ChainTaskState,
    CheckerType,
    ClientEventReceipt,
    GameTaskGroup,
    Task,
)
from games.puzzles import get_puzzle
from games.puzzles.core.check_runtime import CheckerFactory
from games.puzzles.core.quota import revision_quota_message
from games.puzzles.raddle.concurrency import (
    lock_or_create_raddle_state,
    run_raddle_atomic_with_deadlock_retry,
)
from games.puzzles.replacements.grid_puzzle import grid_checker_id
from games.realtime.track_compat import track_actor_task_change
from games.site.http.exceptions import (
    DuplicateAttemptException,
    ReplayedClientEventException,
    TooManyAttemptsException,
)
from games.site.http.middleware.request_timing import timing_phase


def check_attempt(
    attempt,
    *,
    persist_wrong=True,
    timing_request=None,
    bypass_attempt_limit=False,
    client_event_id=None,
):
    """Check and optionally persist an attempt.

    ``client_event_id`` makes a resubmitted move idempotent: when the id
    already produced an attempt for this actor, ``ReplayedClientEventException``
    carries that attempt instead of a second check. A persisted attempt stores
    its receipt in the same transaction.

    ``bypass_attempt_limit`` is reserved for server-generated successful
    attempts (currently Raddle's answer assist).  It does not bypass duplicate
    detection or any checker/state validation; it only allows that authoritative
    answer to be persisted after the player's typo quota is exhausted.
    """
    task = attempt.task
    request_user = getattr(timing_request, 'user', None)
    if (
        attempt.submitted_by_profile_id is None
        and request_user is not None
        and getattr(request_user, 'is_authenticated', False)
    ):
        # The gameplay actor may be the whole team; retain the submitting
        # member separately from Attempt.user, which owns personal progress.
        attempt.submitted_by_profile_id = request_user.pk
    team = attempt.team
    user = getattr(attempt, 'user', None)
    anon_key = getattr(attempt, 'anon_key', None)
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for attempt (set Attempt.game or use a single-linked task group)')
    current_mode = game.get_current_mode(attempt)
    if attempt._state.adding and attempt.task_revision is None:
        attempt.task_revision = task.attempt_revision
    attempt_revision = attempt.task_revision or task.attempt_revision
    modes = ['general']
    if current_mode == 'tournament':
        modes.append('tournament')

    is_chain_task = task.task_type in CHAIN_TASK_TYPES
    puzzle = get_puzzle(task.task_type)

    def _run():
        last_attempt_state = None
        chain_state_row = None

        # Ordinary tasks need the Task row as a stable lock so concurrent
        # submissions cannot both pass duplicate/limit checks.  Chain tasks
        # have a narrower actor-specific ChainTaskState lock below; taking the
        # global Task lock as well makes every teammate block every other
        # teammate on the same ladder, and can end in MySQL lock wait timeout
        # (especially while raddle.ui_state is saving drafts).
        if attempt._state.adding and not is_chain_task:
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            attempt.time = timezone.now()
        elif attempt._state.adding:
            attempt.time = timezone.now()

        if is_chain_task:
            # First-row creation is serialized on Task because MySQL does not
            # materialize Django's conditional UNIQUE constraints here.
            if team is not None:
                chain_actor_key = 'team:{}'.format(team.pk)
            elif user is not None:
                chain_actor_key = 'user:{}'.format(user.pk)
            else:
                chain_actor_key = 'anon:{}'.format(anon_key)
            chain_lookup = {
                'team': team,
                'user': user,
                'anon_key': anon_key,
                'task': task,
                'game': game,
                'game_mode': current_mode,
                'replay_slot': attempt.replay_slot,
                'replay_slot_key': attempt.replay_slot_id or 0,
                'actor_key': chain_actor_key,
            }
            with timing_phase(timing_request, 'chain_state_ensure'):
                chain_state_row = lock_or_create_raddle_state(
                    queryset=ChainTaskState.objects.filter(**chain_lookup),
                    task=task,
                    lookup=chain_lookup,
                    defaults={'state': None},
                )
            with timing_phase(timing_request, 'chain_state_lock'):
                # The helper returns the row locked by primary key. Keep this
                # phase for timing compatibility with existing observability.
                chain_state_row = ChainTaskState.objects.select_for_update().get(pk=chain_state_row.pk)
            last_attempt_state = chain_state_row.state

        if client_event_id is not None:
            replayed = receipt_attempt(client_event_id, attempt)
            if replayed is not None:
                raise ReplayedClientEventException(replayed.pk)

        for mode in modes:
            attempts = Attempt.manager.get_attempts_before(
                team, task, attempt.time, mode, user=user, anon_key=anon_key, game=game,
                replay_slot=attempt.replay_slot,
            )
            revision_attempts = [
                previous for previous in attempts
                if previous.task_revision == attempt_revision
            ]

            # Пустой ChainTaskState после anon-migrate: подтянуть state из попыток
            # текущего режима (не general→tournament — иначе ломается изоляция).
            if (
                is_chain_task
                and last_attempt_state is None
                and mode == current_mode
                and attempts
            ):
                for prev in reversed(attempts):
                    if prev.state:
                        last_attempt_state = prev.state
                        break
            elif not is_chain_task and mode == 'general' and attempts:
                if last_attempt_state is None:
                    last_attempt_state = attempts[-1].state

            if mode == 'tournament' and not bypass_attempt_limit:
                if puzzle is not None:
                    message = puzzle.attempt_limit_message(
                        attempt, revision_attempts, task, team,
                        attempts=attempts,
                        chain_state=chain_state_row.state if chain_state_row else None,
                    )
                else:
                    message = revision_quota_message(attempt, revision_attempts, task, team)
                if message:
                    raise TooManyAttemptsException(message)

            for other_attempt in revision_attempts:
                if puzzle is not None:
                    verdict = puzzle.blocks_duplicate(
                        attempt.text, other_attempt.text,
                        task=task, state_raw=last_attempt_state,
                        other_attempt=other_attempt,
                    )
                    if verdict.blocked:
                        exc = DuplicateAttemptException(
                            'Attempt duplicates one of the previous attempts by this team'
                        )
                        if verdict.attempt_id is not None:
                            exc.attempt_id = verdict.attempt_id
                        raise exc
                elif attempt.text == other_attempt.text:
                    exc = DuplicateAttemptException(
                        'Attempt duplicates one of the previous attempts by this team'
                    )
                    exc.attempt_id = other_attempt.id
                    raise exc

        checker_type = task.get_checker()
        if puzzle is not None and puzzle.checker_id:
            checker_type = CheckerType.objects.get(id=puzzle.checker_id)
        if task.task_type == 'grid-puzzle':
            checker_id = grid_checker_id(task)
            checker_type = CheckerType.objects.get(id=checker_id)
        checker_data = task.checker_data or ''
        # equals / equals_with_possible_spaces читают эталон из checker_data; для «Пропорций»
        # и обычных заданий ответ часто задают только в answer — тогда дублируем его сюда.
        if checker_type.id in ('equals', 'equals_with_possible_spaces'):
            if not checker_data.strip() and (task.answer or '').strip():
                checker_data = task.answer
        checker = CheckerFactory().create_checker(checker_type, checker_data, last_attempt_state)
        check_result = checker.check(attempt.text, attempt)
        attempt.status, attempt.points, attempt.state, attempt.comment = check_result.status, check_result.points, check_result.state, check_result.comment
        # Recompute the hidden checker result on every check; do not retain a
        # stale value after a replay or a checker change.
        attempt.possible_status = None
        if 'tournament' in modes and attempt.status != 'Ok':
            attempt.possible_status = attempt.status
            attempt.status = check_result.tournament_status
        from decimal import Decimal
        scaler = getattr(puzzle, 'scale_points', None) if puzzle is not None else None
        if scaler is not None:
            attempt.points = scaler(task, attempt.points)
        else:
            attempt.points = Decimal(str(attempt.points or 0)) * task.get_points()

        # Store a server-derived cumulative active clock only for an answer
        # that advanced the puzzle. Hint rows and wrong/duplicate submissions
        # must never become per-answer observations.
        if attempt.status in ('Ok', 'Partial'):
            advanced = False
            subject = puzzle
            if subject is None and task.task_type == 'alphabetty':
                from games.puzzles.alphabetty import AlphabettyPuzzle
                subject = AlphabettyPuzzle
            advancer = getattr(subject, 'attempt_advanced', None) if subject is not None else None
            if advancer is not None:
                advanced = advancer(attempt, last_attempt_state)
            if advanced:
                from games.daily.timing import active_time_ms_for_attempt
                attempt.active_time_ms = active_time_ms_for_attempt(
                    game=game,
                    task_group=task.task_group,
                    user=user,
                    anon_key=anon_key,
                    team=team,
                    replay_slot=attempt.replay_slot,
                    now=attempt.time,
                )

        # Auto-checking controls may probe a candidate without turning every typo
        # into an Attempt. The checker still runs under the chain-state lock, but
        # only a result that advances the task is committed.
        if not persist_wrong and check_result.status in ('Wrong', 'Pending'):
            return False

        attempt.save()
        if client_event_id is not None:
            ClientEventReceipt.objects.create(client_event_id=client_event_id, attempt=attempt)

        if chain_state_row is not None:
            chain_state_row.state = attempt.state
            chain_state_row.last_attempt = attempt
            chain_state_row.save(update_fields=['state', 'last_attempt', 'updated_at'])
        return True

    # Chain submissions can deadlock with the collaborative UI state writer.
    # Retry the complete transaction so MySQL has rolled back every lock held
    # by the failed attempt before we evaluate the answer again.
    try:
        if is_chain_task:
            persisted = run_raddle_atomic_with_deadlock_retry(
                _run, label='check_attempt',
            )
        else:
            # The Task row is the stable lock row for ordinary submissions too.
            with transaction.atomic():
                persisted = _run()
    except IntegrityError:
        # A concurrent request with the same client event id committed first;
        # the unique receipt rolled this one back.
        replayed = receipt_attempt(client_event_id, attempt) if client_event_id is not None else None
        if replayed is None:
            raise
        raise ReplayedClientEventException(replayed.pk) from None

    if not persisted:
        return False

    # if some task had tag on this task, recheck it too
    if task.task_type == 'with_tag':
        from games.puzzles.markers.with_tag import WithTagPuzzle
        pair = WithTagPuzzle.dependent_actor(task)
        if pair is None:
            return True
        tag_task, tag_team = pair

        tag_attempts = Attempt.manager.filter(task=tag_task, team=tag_team, game=game)
        rechecked = False
        for tag_attempt in tag_attempts:
            check_attempt(tag_attempt, timing_request=timing_request)
            rechecked = True
        # Attempt.save() deliberately has no broadcast side effect.  Notify the
        # affected team once after the dependent task has been fully rechecked.
        if rechecked:
            track_actor_task_change(
                tag_task,
                team=tag_team,
                game=game,
                reason='task.dependency_rechecked',
            )
    return True
