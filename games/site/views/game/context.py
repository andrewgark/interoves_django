"""Разрешение Game для задания (в т.ч. общие наборы в нескольких играх)."""

import logging
import uuid

from games.daily.page.section import is_scheduled_game, scheduled_number_is_public
from games.models import GameTaskGroup


logger = logging.getLogger('games.views.game_context')


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
        token = (
            request.POST.get('offer_share')
            or request.GET.get('offer_share')
            or ''
        ).strip().lower()
    if not token or task is None or not task.task_group_id:
        return False
    from games.sections.placement_share import share_matches_task
    if share_matches_task(task, token):
        return True
    from games.club.offers.ladder import get_offer_by_share_hash as ladder_offer_by_share_hash
    from games.club.offers.salad import get_offer_by_share_hash as salad_offer_by_share_hash
    from games.club.offers.alphabetty import get_offer_by_share_hash as alphabetty_offer_by_share_hash
    for getter in (
        salad_offer_by_share_hash,
        ladder_offer_by_share_hash,
        alphabetty_offer_by_share_hash,
    ):
        offer = getter(token)
        if offer is not None and offer.task_group_id == task.task_group_id:
            return True
    return False


def _is_word_salad_author(request, game, task):
    """Let a logged-in salad author test their scheduled puzzle before release."""
    if (
        game is None
        or game.id != 'salad'
        or task is None
        or not task.task_group_id
        or not getattr(request.user, 'is_authenticated', False)
    ):
        return False
    return task.task_group.authors.filter(user_id=request.user.pk).exists()


def user_owns_offer_for_link(user, link) -> bool:
    """Whether this player submitted the offer now parked on this schedule slot."""
    if link is None or not getattr(link, 'pk', None):
        return False
    if not getattr(user, 'is_authenticated', False):
        return False
    from games.models import AlphabettyOffer, LadderOffer, WordSaladOffer

    user_id = user.pk
    return any(
        model.objects.filter(accepted_link_id=link.pk, user_id=user_id).exists()
        for model in (LadderOffer, WordSaladOffer, AlphabettyOffer)
    )


def unpublished_scheduled_task_response(request, game, task):
    """Reject authoritative writes to a scheduled release before publication.

    Cleanup is asynchronous now, so pre-publication progress must not enter
    the normal gameplay namespace while the maintenance worker is pending.
    A custom salad or ladder opened by its share hash is already a public puzzle
    and stays playable after it is queued on a future daily number. Salad authors
    can also test their own scheduled puzzle from its daily URL before release.
    """
    if game is None or task is None or not is_scheduled_game(game.id):
        return None
    if getattr(request.user, 'is_staff', False):
        return None
    link = GameTaskGroup.objects.filter(
        game=game, task_group_id=task.task_group_id,
    ).only('number', 'is_deferred', 'share_hash').first()
    if link is not None and link.is_deferred:
        # A parked slot stays off the public schedule. Its author can still
        # open the share link and keep testing the puzzle.
        if user_owns_offer_for_link(request.user, link):
            return None
        return {'status': 'not_published', 'error': 'not_published'}
    if link is not None and not scheduled_number_is_public(game, link.number):
        # Permanent random editions use an opaque hash as their GameTaskGroup
        # number. They are public by design, but are not daily schedule slots.
        is_public_random = False
        if link.share_hash:
            if game.id == 'alphabetty':
                from games.models import RandomAlphabettyGame

                is_public_random = RandomAlphabettyGame.objects.filter(
                    task_group_id=task.task_group_id,
                    share_hash=link.share_hash,
                ).exists()
            elif game.id == 'censorly':
                from games.models import RandomCensorlyGame

                is_public_random = RandomCensorlyGame.objects.filter(
                    task_group_id=task.task_group_id,
                    share_hash=link.share_hash,
                ).exists()
        if is_public_random:
            return None
        if (
            _offer_share_matches_task(request, task)
            or _is_word_salad_author(request, game, task)
        ):
            return None
        incident_id = str(getattr(request, 'interoves_request_id', '') or '')[:64]
        if not incident_id:
            incident_id = uuid.uuid4().hex[:12]
        logger.warning(
            'event=scheduled_task_submission_contract_error '
            'incident_id=%s game=%s task=%s task_group=%s supplied=%s path=%s',
            incident_id,
            game.id,
            task.id,
            task.task_group_id,
            bool((request.POST.get('offer_share') or '').strip()),
            request.path,
        )
        if game.id == 'salad':
            try:
                from games.ops.telegram.bot.alerts import publish_word_salad_submission_error

                publish_word_salad_submission_error(
                    game_id=game.id,
                    incident_id=incident_id,
                )
            except Exception:
                logger.exception('custom scheduled task submission admin notification failed')
        return {'status': 'not_published', 'error': 'not_published'}
    return None
