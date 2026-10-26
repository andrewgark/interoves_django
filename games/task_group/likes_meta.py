"""Like/dislike aggregates for task cards in a task group."""

from games.models import Like


def likes_meta_by_task_id(tasks, *, team, user, anon_key) -> dict:
    """Return per-task like/dislike totals and actor flags (two queries for any number of tasks)."""
    from django.db.models import Count, Q

    task_ids = [t.id for t in tasks]
    out = {
        task_id: {'likes': 0, 'dislikes': 0, 'liked': False, 'disliked': False}
        for task_id in task_ids
    }
    if not task_ids:
        return out
    totals = (
        Like.manager.filter(task_id__in=task_ids)
        .values('task_id')
        .annotate(likes=Count('id', filter=Q(value=1)), dislikes=Count('id', filter=Q(value=-1)))
    )
    for row in totals:
        out[row['task_id']]['likes'] = row['likes']
        out[row['task_id']]['dislikes'] = row['dislikes']
    if team is None and user is None and not anon_key:
        return out
    own = Like.manager.filter(
        task_id__in=task_ids,
        **Like.manager._actor_filter(team=team, user=user, anon_key=anon_key),
    ).values_list('task_id', 'value')
    for task_id, value in own:
        if value == 1:
            out[task_id]['liked'] = True
        elif value == -1:
            out[task_id]['disliked'] = True
    return out


def like_totals_by_task_group_id(task_group_ids) -> dict:
    """``{task_group_id: {likes, dislikes}}`` summed over the group's tasks; groups without reactions are absent."""
    from django.db.models import Count, Q

    rows = (
        Like.manager.filter(task__task_group_id__in=list(task_group_ids))
        .values('task__task_group_id')
        .annotate(likes=Count('id', filter=Q(value=1)), dislikes=Count('id', filter=Q(value=-1)))
    )
    return {
        row['task__task_group_id']: {'likes': row['likes'], 'dislikes': row['dislikes']}
        for row in rows
        if row['likes'] or row['dislikes']
    }
