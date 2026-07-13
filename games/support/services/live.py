from datetime import timedelta
from typing import List, Optional

from django.db.models import Q
from django.utils import timezone

from games.access import game_has_ended, game_has_started
from games.models import Game
from games.support.services.feed import get_activity_feed


def get_live_games(*, include_testing: bool = True) -> List[Game]:
    now = timezone.now()
    qs = Game.objects.filter(start_time__lte=now)
    if not include_testing:
        qs = qs.filter(is_testing=False)
    qs = qs.filter(
        Q(end_time__gte=now) | Q(is_testing=True)
    ).order_by('-start_time')
    return list(qs[:30])


def get_live_feed(*, hours: int = 2, limit: int = 80, game_id: Optional[str] = None):
    game_ids = None
    if game_id:
        game_ids = [game_id]
    else:
        game_ids = [g.id for g in get_live_games()]
    if not game_ids:
        return [], []

    items = []
    for gid in game_ids:
        items.extend(get_activity_feed(game_id=gid, hours=hours, limit=limit).items)
    items.sort(key=lambda row: row.time or timezone.now(), reverse=True)
    return get_live_games(), items[:limit]
