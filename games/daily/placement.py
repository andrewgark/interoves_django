"""Canonical resolution of a daily placement from its public URL segment."""

from games.models import GameTaskGroup
from games.placement_share import is_share_hash_segment, placement_by_share_hash


def resolve_daily_placement(game, number):
    """Return a placement addressed by either its number or share hash."""
    segment = str(number or '').strip()
    if is_share_hash_segment(segment):
        return placement_by_share_hash(game, segment)
    return GameTaskGroup.objects.select_related('task_group').filter(
        game=game,
        number=segment,
    ).first()


def is_random_alphabetty_placement(placement):
    """Whether a placement is a permanent random Alphabetty game."""
    from games.models import RandomAlphabettyGame

    return bool(
        placement is not None
        and placement.game_id == 'alphabetty'
        and RandomAlphabettyGame.objects.filter(
            task_group_id=placement.task_group_id,
        ).exists()
    )
