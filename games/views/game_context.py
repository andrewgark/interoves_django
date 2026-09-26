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


def _offer_share_matches_task(request, task):
    """True when this POST proves the player opened the custom offer, not the future daily number."""
    token = ''
    if request is not None:
        token = (request.POST.get('offer_share') or '').strip().lower()
    if not token or task is None or not task.task_group_id:
        return False
    from games.ladder_offer import get_offer_by_share_hash as ladder_offer_by_share_hash
    from games.word_salad_offer import get_offer_by_share_hash as salad_offer_by_share_hash
    for getter in (salad_offer_by_share_hash, ladder_offer_by_share_hash):
        offer = getter(token)
        if offer is not None and offer.task_group_id == task.task_group_id:
            return True
    return False


def unpublished_scheduled_task_response(request, game, task):
    """Reject authoritative writes to a scheduled release before publication.

    Cleanup is asynchronous now, so pre-publication progress must not enter
    the normal gameplay namespace while the maintenance worker is pending.
    A custom salad or ladder opened by its share hash is already a public puzzle
    and stays playable after it is queued on a future daily number.
    """
    if game is None or task is None or not is_scheduled_game(game.id):
        return None
    if getattr(request.user, 'is_staff', False):
        return None
    link = GameTaskGroup.objects.filter(
        game=game, task_group_id=task.task_group_id,
    ).only('number').first()
    if link is not None and not scheduled_number_is_public(game, link.number):
        if _offer_share_matches_task(request, task):
            return None
        return {'status': 'not_published', 'error': 'not_published'}
    return None
