"""Разрешение Game для задания (в т.ч. общие наборы в нескольких играх)."""

from games.daily_section import is_scheduled_game, scheduled_number_is_public
from games.models import GameTaskGroup


def game_from_request_for_task(request, task):
    """
    game_id из POST, GET, заголовка; иначе единственная игра, куда входит набор заданий.
    """
    gid = (
        (request.POST.get('game_id') or request.GET.get('game_id') or '').strip()
        or (request.headers.get('X-Interoves-Game') or '').strip()
    )
    return GameTaskGroup.resolve_game_for_task(task, game_id=gid or None)


def unpublished_scheduled_task_response(request, game, task):
    """Reject authoritative writes to a scheduled release before publication.

    Cleanup is asynchronous now, so pre-publication progress must not enter
    the normal gameplay namespace while the maintenance worker is pending.
    """
    if game is None or task is None or not is_scheduled_game(game.id):
        return None
    if getattr(request.user, 'is_staff', False):
        return None
    link = GameTaskGroup.objects.filter(
        game=game, task_group_id=task.task_group_id,
    ).only('number').first()
    if link is not None and not scheduled_number_is_public(game, link.number):
        return {'status': 'not_published', 'error': 'not_published'}
    return None
