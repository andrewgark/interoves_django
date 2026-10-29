"""What follows a move: the game counts as started, and may now be complete."""

from __future__ import annotations

from dataclasses import dataclass, field

from games.daily.completion import daily_completion_effects_for_game
from games.daily.completion.completion_coordinator import complete_logical_game
from games.ops.analytics.events import (
    PlayerCompletedGame,
    analytics_game_kind,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.site.http.middleware.request_timing import timing_phase


@dataclass
class MoveEffects:
    analytics_events: list = field(default_factory=list)
    daily_timing: dict | None = None
    replay_available: bool = False


def apply_move_effects(ctx, *, started, may_complete, source, observer=None):
    """Register the started game and complete the logical game when it is solved.

    ``started`` says whether this move starts the game for analytics;
    ``may_complete`` whether it can have solved the task group (the caller
    knows which attempt statuses and states qualify). ``source`` names the
    caller in completion logs. ``observer`` is the request whose timing phases
    are recorded, if any.
    """
    effects = MoveEffects()
    if started and ctx.replay_slot is None:
        with timing_phase(observer, 'analytics_started'):
            effects.analytics_events.extend(register_started_game(
                **ctx.actor,
                analytics_user=ctx.analytics_user,
                task=ctx.task,
                game=ctx.game,
            ))
    if not (may_complete and analytics_game_kind(ctx.game)):
        return effects
    with timing_phase(observer, 'completion_check'):
        complete = is_task_group_complete(
            task_group=ctx.task.task_group,
            game=ctx.game,
            **ctx.actor,
            mode=ctx.mode,
            replay_slot=ctx.replay_slot,
        )
    if not complete:
        return effects
    completion = complete_logical_game(
        actor=ctx.actor,
        game=ctx.game,
        task_group=ctx.task.task_group,
        task=ctx.task,
        replay_slot=ctx.replay_slot,
        run_id=ctx.run_id,
        analytics_user=ctx.analytics_user,
        result=PlayerCompletedGame.RESULT_SOLVED,
        mode=ctx.mode,
        source=source,
    )
    if completion is None:
        return effects
    with timing_phase(observer, 'analytics_completed'):
        # Completion analytics and timing exist only outside a replay; the
        # adapter skips publishing there.
        applied = daily_completion_effects_for_game(
            ctx.game.id,
            completion,
            replay_slot=ctx.replay_slot,
            publish_analytics=publish_completion_analytics,
            analytics_kwargs={
                'record': completion['record'],
                'created': completion['created'],
                'user': ctx.user,
                'anon_key': ctx.anon_key,
                'analytics_user': ctx.analytics_user,
                'game': ctx.game,
                'task_group': ctx.task.task_group,
            },
        )
    effects.replay_available = ctx.replay_slot is None
    effects.daily_timing = applied['timing']
    effects.analytics_events.extend(applied['analytics_events'])
    return effects
