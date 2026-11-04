"""Which daily puzzle to open for a newcomer (ads, /start/).

Today's puzzle can be a 5★ one, and a first puzzle that hard loses most new
players. Production data for newcomers' first puzzle (Aug 15 – Oct 26, 2026):

    stars  finished  returned within 7 days
    1★     79%       22%
    2★     77%       33%
    3★     68%       39%
    4★     56%       29%
    5★     38%       23%

Too easy does not hook, too hard scares off; 3★ keeps the most players. So a
newcomer gets the published puzzle closest to 3★ from the free window (the
latest seven, never behind the Club paywall), skipping puzzles the visitor
has already finished.

Цензурка is the exception: its 3★ articles lose newcomers whose first game it
is (Aug 15 – Nov 4, 2026):

    stars  n   finished  returned within 7 days
    2★     30  67%       23%
    3★     62  52%       18%
    4★     51  63%       31%

so a Цензурка newcomer gets a 2★ article first, then a 4★ one.
"""

from games.club.access.archive import FREE_ARCHIVE_COUNT
from games.daily.difficulty.core import get_cached_game_difficulties
from games.daily.registry import get_daily_game
from games.models import GameTaskGroup, PlayerCompletedGame

NEWCOMER_GAME_IDS = ('ladder', 'salad', 'alphabetty', 'censorly')
# Preference by star rating; 1★ and 5★ are never chosen.
STAR_PREFERENCE = (3, 2, 4)
STAR_PREFERENCE_BY_GAME = {'censorly': (2, 4, 3)}


def free_published_placements(game):
    """The latest FREE_ARCHIVE_COUNT published numbered puzzles, newest first."""
    definition = get_daily_game(game.id)
    if definition is None:
        return []
    links = GameTaskGroup.objects.filter(game=game, is_deferred=False)
    published = [
        link for link in definition.schedule.filter_published(links, game)
        if str(link.number).isdigit()
    ]
    published.sort(key=lambda link: int(link.number), reverse=True)
    return published[:FREE_ARCHIVE_COUNT]


def newcomer_placement(game, *, user=None, anon_key=None):
    """Preferred-difficulty placement among free puzzles the actor has not finished.

    None when no candidate has a visible rating yet; callers fall back to the
    latest puzzle.
    """
    candidates = free_published_placements(game)
    if not candidates:
        return None
    finished = PlayerCompletedGame.objects.filter(
        task_group_id__in=[link.task_group_id for link in candidates],
    )
    if user is not None and getattr(user, 'is_authenticated', False):
        finished = finished.filter(user=user)
    elif anon_key:
        finished = finished.filter(anon_key=anon_key, user__isnull=True)
    else:
        finished = finished.none()
    done = set(finished.values_list('task_group_id', flat=True))
    difficulties = get_cached_game_difficulties(candidates)
    preference = STAR_PREFERENCE_BY_GAME.get(game.id, STAR_PREFERENCE)
    ranked = []
    for order, link in enumerate(candidates):
        if link.task_group_id in done:
            continue
        stars = (difficulties.get(link.pk) or {}).get('stars')
        if stars in preference:
            ranked.append((preference.index(stars), order, link))
    if not ranked:
        return None
    return min(ranked, key=lambda item: item[:2])[2]
