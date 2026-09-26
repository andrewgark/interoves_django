"""Stable public hash for a scheduled daily slot.

Offer share links stay the canonical URL when a user proposal exists.
Slots created directly in the schedule get their own hash on GameTaskGroup
and render the same play page.
"""

from __future__ import annotations

import re
import secrets

from games.section_paths import section_play_path

_SHARE_HASH_RE = re.compile(r'^[a-f0-9]{16,32}$')
_SCHEDULED_GAME_IDS = frozenset({'ladder', 'salad', 'alphabetty', 'week_task'})
_RESERVED_SEGMENTS = frozenset({
    'today', 'last', 'progress', 'results', 'replay', 'timing', 'live-state',
    'guess', 'state', 'prefix', 'suggest', 'hint',
})


def is_share_hash_segment(segment: str) -> bool:
    seg = (segment or '').strip().lower()
    if not seg or seg in _RESERVED_SEGMENTS:
        return False
    return bool(_SHARE_HASH_RE.match(seg))


def allocate_share_hash() -> str:
    from games.models import AlphabettyOffer, GameTaskGroup, LadderOffer, WordSaladOffer

    for _ in range(20):
        token = secrets.token_hex(8)
        if token in _RESERVED_SEGMENTS or token.isdigit():
            continue
        if GameTaskGroup.objects.filter(share_hash=token).exists():
            continue
        if (
            WordSaladOffer.objects.filter(share_hash=token).exists()
            or LadderOffer.objects.filter(share_hash=token).exists()
            or AlphabettyOffer.objects.filter(share_hash=token).exists()
        ):
            continue
        return token
    raise RuntimeError('Не удалось сгенерировать share_hash')


def adopt_offer_share_hash(link, offer_hash: str) -> None:
    """Keep the proposal URL and the schedule slot on the same token."""
    token = (offer_hash or '').strip().lower()
    if not token or not link or not getattr(link, 'pk', None):
        return
    if (link.share_hash or '') == token:
        return
    from games.models import GameTaskGroup

    GameTaskGroup.objects.filter(pk=link.pk).update(share_hash=token)
    link.share_hash = token


def placement_by_share_hash(game, segment: str):
    from games.models import GameTaskGroup

    token = (segment or '').strip().lower()
    if not is_share_hash_segment(token):
        return None
    return (
        GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
        .filter(game=game, share_hash=token)
        .first()
    )


def share_matches_task(task, token: str) -> bool:
    if task is None or not getattr(task, 'task_group_id', None):
        return False
    seg = (token or '').strip().lower()
    if not is_share_hash_segment(seg):
        return False
    from games.models import GameTaskGroup

    return GameTaskGroup.objects.filter(
        task_group_id=task.task_group_id,
        share_hash=seg,
    ).exists()


def may_open_unpublished_number(user) -> bool:
    return bool(getattr(user, 'is_authenticated', False) and getattr(user, 'is_staff', False))


def site_urls_by_task_group(game_id: str, task_group_ids) -> dict[int, str]:
    """Real site page for a daily slot: offer hash, else the slot hash."""
    ids = [pk for pk in task_group_ids if pk]
    if not ids:
        return {}
    from games.models import AlphabettyOffer, GameTaskGroup, LadderOffer, WordSaladOffer

    paths: dict[int, str] = {}
    links = GameTaskGroup.objects.filter(
        game_id=game_id, task_group_id__in=ids,
    ).only('task_group_id', 'share_hash', 'number')
    for link in links:
        token = (link.share_hash or '').strip()
        if token:
            paths[link.task_group_id] = section_play_path(game_id, token)
        elif link.task_group_id not in paths:
            paths[link.task_group_id] = section_play_path(game_id, link.number)

    offer_models = {
        'salad': WordSaladOffer,
        'ladder': LadderOffer,
        'alphabetty': AlphabettyOffer,
    }
    model = offer_models.get(game_id)
    if model is None:
        return paths
    offers = model.objects.filter(task_group_id__in=ids).exclude(share_hash='')
    only_fields = ['task_group_id', 'share_hash']
    if game_id == 'salad':
        offers = offers.filter(kind=WordSaladOffer.KIND_FULL)
        only_fields.append('kind')
    for offer in offers.only(*only_fields):
        url = offer.play_url()
        if url:
            paths[offer.task_group_id] = url
    return paths


def scheduled_game_ids():
    return _SCHEDULED_GAME_IDS
