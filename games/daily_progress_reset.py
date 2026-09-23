"""Reset progress accidentally created before the current daily release."""

from __future__ import annotations

from collections import defaultdict
import logging
import time

from django.contrib.auth.models import User
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from games.daily_section import current_number_for, publish_at_for, schedule_for
from games.models import (
    Attempt, ChainTaskState, DailyGameDifficulty, DailyResultProjection,
    DailyResultProjectionState, DailySolveTiming, Game, GameTaskGroup,
    HintAttempt, PlayerCompletedGame, PlayerStartedGame, ReplaySlot, Task, Team,
)


DAILY_RESET_GAME_IDS = ('ladder', 'alphabetty', 'salad')
CHAIN_TYPES = {'wall', 'replacements_lines', 'raddle', 'alphabetty', 'word_salad'}
MYSQL_DEADLOCK_ERRNO = 1213
RESET_DEADLOCK_ATTEMPTS = 3

logger = logging.getLogger(__name__)


def _empty_reset_result():
    return {
        'reset': False,
        'attempts': 0,
        'hint_attempts': 0,
        'timings': 0,
        'started': 0,
        'completed': 0,
        'projections': 0,
        'projection_states': 0,
        'chains': 0,
        'rechecked': False,
    }


def _is_mysql_deadlock(exc):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, 'args', None)
        if args:
            try:
                return int(args[0]) == MYSQL_DEADLOCK_ERRNO
            except (TypeError, ValueError):
                pass
        current = getattr(current, '__cause__', None)
    return False


def _actor_objects(keys):
    teams = Team.objects.in_bulk({key[0] for key in keys if key[0]})
    users = User.objects.in_bulk({key[1] for key in keys if key[1]})
    slots = ReplaySlot.objects.in_bulk({key[3] for key in keys if key[3]})
    for team_id, user_id, anon_key, replay_slot_id in keys:
        team = teams.get(team_id) if team_id else None
        user = users.get(user_id) if user_id else None
        slot = slots.get(replay_slot_id) if replay_slot_id else None
        if (team_id and team is None) or (user_id and user is None) or (replay_slot_id and slot is None):
            continue
        yield team, user, anon_key, slot


def _rebuild_chain(task, game, team, user, anon_key, replay_slot):
    from games.recheck import recheck_chain_task, recheck_word_salad_actor

    kwargs = dict(task=task, game=game, team=team, user=user, anon_key=anon_key,
                  replay_slot=replay_slot, notify=False)
    if task.task_type == 'word_salad':
        recheck_word_salad_actor(**kwargs)
    else:
        recheck_chain_task(**kwargs)


def _has_postpublication_attempt(
    *, task_id, game, team, user, anon_key, replay_slot, published_at,
):
    return Attempt.manager.filter(
        task_id=task_id,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        replay_slot=replay_slot,
        time__gte=published_at,
    ).exists()


def _rebuild_projection_after_reset(game_id, task_group_id):
    """Rebuild the derived release projection after cleanup commits."""
    from games.daily_result_projection import refresh_daily_result_projection
    from games.models import Game, TaskGroup

    game = Game.objects.filter(pk=game_id).first()
    task_group = TaskGroup.objects.filter(pk=task_group_id).first()
    if game is None or task_group is None:
        return
    try:
        refresh_daily_result_projection(game, task_group)
    except Exception:
        logger.exception(
            'daily_progress_reset projection rebuild failed game=%s task_group=%s',
            game_id,
            task_group_id,
        )


@transaction.atomic
def reset_daily_release_progress(placement, *, now=None):
    """Delete official and replay attempts before publication and rebuild state."""
    now = now or timezone.now()
    placement = GameTaskGroup.objects.select_for_update().select_related(
        'game', 'task_group',
    ).get(pk=placement.pk)
    published_at = publish_at_for(placement.game, placement.number)
    if published_at is None or published_at > now:
        return _empty_reset_result()

    tasks = list(Task.objects.filter(task_group=placement.task_group))
    task_ids = [task.pk for task in tasks]
    if not task_ids:
        return _empty_reset_result()

    old_attempts = Attempt.manager.filter(
        game=placement.game, task_id__in=task_ids, time__lt=published_at,
    )
    old_hint_attempts = HintAttempt.objects.filter(
        hint__task_id__in=task_ids, time__lt=published_at,
    )
    stale_timing = DailySolveTiming.objects.filter(
        game=placement.game, task_group=placement.task_group,
        created_at__lt=published_at,
        updated_at__lt=published_at,
    )
    stale_started = PlayerStartedGame.objects.filter(
        game=placement.game, task_group=placement.task_group,
        started_at__lt=published_at,
    )
    stale_completed = PlayerCompletedGame.objects.filter(
        game=placement.game, task_group=placement.task_group,
        completed_at__lt=published_at,
    )
    stale_states = ChainTaskState.objects.filter(
        game=placement.game, task_id__in=task_ids, updated_at__lt=published_at,
    )
    stale_projection = DailyResultProjection.objects.filter(
        game=placement.game, task_group=placement.task_group,
    )
    if not any(qs.exists() for qs in (
        old_attempts, old_hint_attempts, stale_timing, stale_started,
        stale_completed, stale_states,
    )):
        return _empty_reset_result()

    chain_ids = {task.pk for task in tasks if task.task_type in CHAIN_TYPES}
    actors = defaultdict(set)
    for task_id, team_id, user_id, anon_key, replay_slot_id in old_attempts.values_list(
        'task_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
    ):
        if task_id in chain_ids:
            actors[task_id].add(_actor_key(team_id, user_id, anon_key, replay_slot_id))
    for task_id, team_id, user_id, anon_key, replay_slot_id in stale_states.values_list(
        'task_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
    ):
        if task_id in chain_ids:
            actors[task_id].add(_actor_key(team_id, user_id, anon_key, replay_slot_id))

    counts = {
        'attempts': old_attempts.count(),
        'hint_attempts': old_hint_attempts.count(),
        'timings': stale_timing.count(),
        'started': stale_started.count(),
        'completed': stale_completed.count(),
        'projections': stale_projection.count(),
        'projection_states': DailyResultProjectionState.objects.filter(
            game=placement.game, task_group=placement.task_group,
        ).count(),
    }
    old_attempts.delete()
    old_hint_attempts.delete()
    stale_timing.delete()
    stale_started.delete()
    stale_completed.delete()
    # Projections are derived. Keep the last committed projection visible
    # until the post-commit rebuild succeeds; never create a missing/empty
    # projection window inside the canonical cleanup transaction.
    DailyGameDifficulty.objects.filter(placement=placement).update(
        data_revision=F('data_revision') + 1, dirty=True,
    )
    transaction.on_commit(
        lambda game_id=placement.game_id, task_group_id=placement.task_group_id:
        _rebuild_projection_after_reset(game_id, task_group_id)
    )

    task_by_id = {task.pk: task for task in tasks}
    rebuilt = 0
    rechecked = False
    for task_id, actor_keys in actors.items():
        for team, user, anon_key, replay_slot in _actor_objects(actor_keys):
            ChainTaskState.objects.filter(
                task_id=task_id, game=placement.game,
                team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
            ).delete()
            if not _has_postpublication_attempt(
                task_id=task_id,
                game=placement.game,
                team=team,
                user=user,
                anon_key=anon_key,
                replay_slot=replay_slot,
                published_at=published_at,
            ):
                continue
            _rebuild_chain(task_by_id[task_id], placement.game, team, user, anon_key, replay_slot)
            rebuilt += 1
            rechecked = True
    return {
        'reset': True,
        **counts,
        'chains': rebuilt,
        'rechecked': rechecked,
    }


def reset_current_daily_release_progress(*, now=None, game_ids=DAILY_RESET_GAME_IDS):
    now = now or timezone.now()
    game_ids = tuple(game_ids) if game_ids else DAILY_RESET_GAME_IDS
    results = []
    for game in Game.objects.filter(project_id='sections', id__in=game_ids):
        if schedule_for(game.id) is None:
            continue
        number = current_number_for(game, now=now)
        placement = GameTaskGroup.objects.filter(game=game, number=str(number)).first() if number else None
        if placement is None:
            continue
        started_at = time.perf_counter()
        for attempt in range(1, RESET_DEADLOCK_ATTEMPTS + 1):
            try:
                result = reset_daily_release_progress(placement, now=now)
                break
            except OperationalError as exc:
                if not _is_mysql_deadlock(exc):
                    raise
                if attempt >= RESET_DEADLOCK_ATTEMPTS:
                    logger.warning(
                        'daily_progress_reset deadlock exhausted attempts=%s game=%s number=%s',
                        attempt,
                        game.id,
                        number,
                    )
                    result = None
                    break
                logger.warning(
                    'daily_progress_reset deadlock retry attempt=%s/%s game=%s number=%s',
                    attempt,
                    RESET_DEADLOCK_ATTEMPTS,
                    game.id,
                    number,
                )
        if result is None:
            continue
        logger.info(
            'daily_progress_reset finished game=%s number=%s published_at=%s '
            'duration_ms=%.1f reset=%s attempts=%s hint_attempts=%s timings=%s started=%s '
            'completed=%s projections=%s projection_states=%s chains=%s rechecked=%s',
            game.id,
            number,
            publish_at_for(game, number),
            (time.perf_counter() - started_at) * 1000.0,
            result.get('reset', False),
            result.get('attempts', 0),
            result.get('hint_attempts', 0),
            result.get('timings', 0),
            result.get('started', 0),
            result.get('completed', 0),
            result.get('projections', 0),
            result.get('projection_states', 0),
            result.get('chains', 0),
            result.get('rechecked', False),
        )
        if result['reset']:
            results.append({'game_id': game.id, 'number': str(number), **result})
    return results
