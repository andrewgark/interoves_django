"""Разрешение Game для задания (в т.ч. общие наборы в нескольких играх)."""

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
