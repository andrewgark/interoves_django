"""/start/ A/B test: which daily game the page recommends first.

Newcomers who began with Лесенка or Алфавитка came back within a week more
often (40%, 43%) than those who began with Салатик (23%), the game /start/
has always recommended — but Салатик also got the low-intent ad traffic. The
variant is stored per actor on its first /start/ view and carried through
sign-up, so retention per arm can be compared server-side.
"""

import hashlib

from django.utils import timezone

from games.models import PlayerAnalyticsState
from games.ops.analytics.persistence import create_or_reread_analytics_row

START_VARIANTS = ('salad', 'ladder', 'alphabetty')
DEFAULT_VARIANT = 'salad'


def _seed_variant(seed: str) -> str:
    digest = hashlib.sha256(seed.encode('utf-8')).hexdigest()
    return START_VARIANTS[int(digest, 16) % len(START_VARIANTS)]


def start_variant(*, user=None, anon_key=None) -> str:
    """The actor's arm; assigned and stored on the first call."""
    if user is not None and getattr(user, 'is_authenticated', False):
        lookup = {'team': None, 'user': user, 'anon_key': None}
        seed = f'user:{user.pk}'
    elif anon_key:
        lookup = {'team': None, 'user': None, 'anon_key': str(anon_key)}
        seed = f'anon:{anon_key}'
    else:
        return DEFAULT_VARIANT
    state, _ = create_or_reread_analytics_row(PlayerAnalyticsState, lookup=lookup)
    if state.start_variant in START_VARIANTS:
        return state.start_variant
    variant = _seed_variant(seed)
    PlayerAnalyticsState.objects.filter(pk=state.pk, start_variant='').update(
        start_variant=variant, start_viewed_at=timezone.now(),
    )
    state.refresh_from_db(fields=['start_variant'])
    return state.start_variant or variant
