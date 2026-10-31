"""Likes and dislikes of single ladder clues."""

from django.db import transaction
from django.db.models import Count, Q

from games.models import RaddleClueLike, Task


def _actor_filter(team=None, user=None, anon_key=None):
    if team is not None:
        return {'team': team, 'user__isnull': True, 'anon_key__isnull': True}
    if user is not None:
        return {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
    return {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}


def set_clue_reaction(task, hint_index, value, clue_text='', *, team=None, user=None, anon_key=None):
    """Replace the actor's reaction to one clue. ``value`` 0 removes it."""
    if value not in (-1, 0, 1):
        raise ValueError('Reaction value must be -1, 0, or 1')
    if sum(actor is not None for actor in (team, user, anon_key)) != 1:
        raise ValueError('Exactly one reaction actor is required')
    actor_filter = _actor_filter(team=team, user=user, anon_key=anon_key)
    with transaction.atomic():
        # Same lock as task likes: serializes reaction changes for one task.
        Task.objects.select_for_update().only('pk').get(pk=task.pk)
        RaddleClueLike.objects.filter(task=task, hint_index=hint_index, **actor_filter).delete()
        if value:
            RaddleClueLike.objects.create(
                task=task,
                hint_index=hint_index,
                clue_text=clue_text,
                team=team,
                user=user,
                anon_key=anon_key,
                value=value,
            )


def clue_reactions_meta(task, *, team=None, user=None, anon_key=None):
    """``{hint_index: {likes, dislikes, liked, disliked}}`` for clues with any reaction."""
    out = {}
    totals = (
        RaddleClueLike.objects.filter(task=task)
        .values('hint_index')
        .annotate(likes=Count('id', filter=Q(value=1)), dislikes=Count('id', filter=Q(value=-1)))
    )
    for row in totals:
        out[row['hint_index']] = {
            'likes': row['likes'],
            'dislikes': row['dislikes'],
            'liked': False,
            'disliked': False,
        }
    if team is None and user is None and not anon_key:
        return out
    own = RaddleClueLike.objects.filter(
        task=task, **_actor_filter(team=team, user=user, anon_key=anon_key),
    ).values_list('hint_index', 'value')
    for hint_index, value in own:
        meta = out.get(hint_index)
        if meta is not None:
            meta['liked'] = value == 1
            meta['disliked'] = value == -1
    return out


_EMPTY_META = {'likes': 0, 'dislikes': 0, 'liked': False, 'disliked': False}


def attach_clue_reactions(ui, task, *, team=None, user=None, anon_key=None, meta=None):
    """Put ``reaction`` on each used clue: only a solved transition can be judged.

    ``meta`` is ``clue_reactions_meta`` already loaded for this actor, for a
    caller that draws several boards of one ladder.
    """
    used = ui.get('used_hints') or []
    if not used:
        return
    if meta is None:
        meta = clue_reactions_meta(task, team=team, user=user, anon_key=anon_key)
    for hint in used:
        hint['reaction'] = meta.get(hint['index'], _EMPTY_META)


@transaction.atomic
def migrate_anon_clue_likes(user, anon_key, *, batch_size=None):
    """Move guest clue reactions onto ``user``; a reaction the user already has wins.

    Returns how many guest rows were handled (moved or dropped).
    """
    if not user or not anon_key:
        return 0
    qs = RaddleClueLike.objects.select_for_update().filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    ).order_by('id')
    anon_rows = list(qs[:batch_size] if batch_size else qs)
    if not anon_rows:
        return 0
    taken = set(
        RaddleClueLike.objects.filter(
            user=user, task_id__in={row.task_id for row in anon_rows},
        ).values_list('task_id', 'hint_index')
    )
    for row in anon_rows:
        if (row.task_id, row.hint_index) in taken:
            row.delete()
            continue
        row.user = user
        row.anon_key = None
        row.save(update_fields=['user', 'anon_key'])
        taken.add((row.task_id, row.hint_index))
    return len(anon_rows)


@transaction.atomic
def merge_user_clue_likes(target, source):
    """Account merge: move ``source`` clue reactions onto ``target`` unless it has its own."""
    rows = list(RaddleClueLike.objects.select_for_update().filter(user=source).order_by('id'))
    taken = set(
        RaddleClueLike.objects.filter(user=target).values_list('task_id', 'hint_index')
    )
    for row in rows:
        if (row.task_id, row.hint_index) in taken:
            row.delete()
            continue
        row.user = target
        row.save(update_fields=['user'])
        taken.add((row.task_id, row.hint_index))
    return len(rows)
