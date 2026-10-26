"""Like/dislike aggregates for task cards in a task group."""

from games.models import Like


def likes_meta_by_task_id(tasks, *, team, user, anon_key) -> dict:
    """Return per-task like/dislike totals and actor flags."""
    out = {}
    for t in tasks:
        out[t.id] = {
            'likes': Like.manager.get_total_likes(t),
            'dislikes': Like.manager.get_total_dislikes(t),
            'liked': Like.manager.actor_has_like(t, team=team, user=user, anon_key=anon_key),
            'disliked': Like.manager.actor_has_dislike(t, team=team, user=user, anon_key=anon_key),
        }
    return out
