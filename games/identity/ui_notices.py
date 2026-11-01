"""One-off UI notices a signed-in player has already seen or turned off.

The page also mirrors these keys in localStorage, so anonymous players get
the same "show once" behaviour on one browser; the server copy makes it
follow a signed-in player across devices.
"""

from django.db import IntegrityError, transaction

from games.models import UiNoticeSeen

# The "stuck? take a hint" callout is shown once per daily game kind.
STUCK_NUDGE_GAMES = ('ladder', 'salad', 'alphabetty', 'censorly')
# "Больше не подсказывать": no stuck callouts or glows in any game.
STUCK_NUDGE_OFF = 'stuck_nudge.off'

KNOWN_KEYS = frozenset(
    ['stuck_nudge.' + game_id for game_id in STUCK_NUDGE_GAMES] + [STUCK_NUDGE_OFF]
)


def seen_keys(user):
    if user is None or not user.is_authenticated:
        return []
    return sorted(UiNoticeSeen.objects.filter(user=user).values_list('key', flat=True))


def mark_seen(user, key):
    """Remember ``key`` for ``user``. Returns False for an unknown key."""
    if key not in KNOWN_KEYS:
        return False
    if UiNoticeSeen.objects.filter(user=user, key=key).exists():
        return True
    try:
        with transaction.atomic():
            UiNoticeSeen.objects.create(user=user, key=key)
    except IntegrityError:
        # A second tab marked the same key first.
        pass
    return True
