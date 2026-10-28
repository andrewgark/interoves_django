"""Canonical resolution of a daily placement from its public URL segment."""

from games.daily.page.section import is_scheduled_game, scheduled_number_is_public
from games.models import GameTaskGroup
from games.sections.placement_share import is_share_hash_segment, placement_by_share_hash


def resolve_daily_placement(game, number):
    """Return a placement addressed by either its number or share hash."""
    segment = str(number or '').strip()
    if is_share_hash_segment(segment):
        return placement_by_share_hash(game, segment)
    return GameTaskGroup.objects.select_related('task_group').filter(
        game=game,
        number=segment,
        is_deferred=False,
    ).first()


def resolve_accessible_daily_placement(game, segment, *, user=None):
    """Resolve a public share URL or an allowed numbered daily URL.

    Opaque placement hashes are public, including before their schedule date.
    Numbered URLs follow the game's publication date and staff preview policy.
    Deferred placements are never addressable through a public hash.
    """
    value = str(segment or '').strip()
    if not value:
        return None
    if is_share_hash_segment(value):
        placement = placement_by_share_hash(game, value)
        if placement is None or placement.is_deferred:
            return None
        return placement
    if is_scheduled_game(game.id) and not scheduled_number_is_public(game, value):
        from games.sections.placement_share import may_open_unpublished_number

        if not may_open_unpublished_number(user):
            return None
    return GameTaskGroup.objects.select_related('task_group').filter(
        game=game,
        number=value,
        is_deferred=False,
    ).first()


def is_random_daily_placement(placement):
    """Share-hash random editions that may expose daily statistics."""
    if placement is None:
        return False
    from games.models import RandomAlphabettyGame, RandomCensorlyGame

    model = {
        'alphabetty': RandomAlphabettyGame,
        'censorly': RandomCensorlyGame,
    }.get(placement.game_id)
    if model is None:
        return False
    return model.objects.filter(task_group_id=placement.task_group_id).exists()
