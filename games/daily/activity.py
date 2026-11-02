"""How many distinct days a player has finished daily games.

Used to time in-product prompts (sign up, join the Club) by engagement rather
than by raw game counts: a player who solves four games once is not a regular.
Days are Moscow calendar dates, like the rest of the daily schedule.
"""

from zoneinfo import ZoneInfo

from django.core.cache import cache

from games.models import PlayerCompletedGame

MOSCOW = ZoneInfo('Europe/Moscow')
CACHE_SECONDS = 15 * 60


def _cache_key(user_id, anon_key):
    return 'daily-active-days:{}'.format('u{}'.format(user_id) if user_id else 'a{}'.format(anon_key))


def active_days(*, user=None, anon_key=None) -> int:
    """Distinct Moscow dates with at least one completed game for the actor."""
    user_id = getattr(user, 'pk', None)
    if not user_id and not anon_key:
        return 0
    key = _cache_key(user_id, anon_key)
    cached = cache.get(key)
    if cached is not None:
        return cached
    if user_id:
        rows = PlayerCompletedGame.objects.filter(user_id=user_id)
    else:
        rows = PlayerCompletedGame.objects.filter(anon_key=anon_key, user__isnull=True, team__isnull=True)
    days = len({moment.astimezone(MOSCOW).date() for moment in rows.values_list('completed_at', flat=True)})
    cache.set(key, days, CACHE_SECONDS)
    return days

