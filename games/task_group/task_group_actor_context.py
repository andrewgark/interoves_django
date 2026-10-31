"""Bulk attempts and gameplay context tokens for a task group."""

from games.models import Attempt
from games.site.session.gameplay_context import issue_gameplay_context


def bulk_attempts_and_gameplay_tokens(
    game,
    tasks,
    *,
    team,
    user,
    anon_key,
    mode,
    replay_slot=None,
):
    """Return (attempts_info_by_task_id, gameplay_context_tokens)."""
    attempts_info_by_task_id = Attempt.manager.get_bulk_actor_attempts_infos(
        [t.id for t in tasks],
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        game=game,
        replay_slot=replay_slot,
        tasks=tasks,
    )
    gameplay_context_tokens = {
        t.id: issue_gameplay_context(
            task=t, game=game, team=team, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        )
        for t in tasks
    }
    return attempts_info_by_task_id, gameplay_context_tokens
