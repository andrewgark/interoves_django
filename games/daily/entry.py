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

Within a star rating, a Салатик with fewer words comes first (Nov 3–6: of
125 newcomers who left a Салатик unfinished, 45 had found six or more
words and stalled on the last ones), then the puzzle players liked most.
"""

from django.db.models import Count, Q

from games.club.access.archive import FREE_ARCHIVE_COUNT
from games.daily.difficulty.core import get_cached_game_difficulties
from games.daily.registry import get_daily_game
from games.models import GameTaskGroup, Like, PlayerCompletedGame, Task

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


def like_scores(links):
    """Laplace-smoothed share of likes per placement: (likes+1)/(votes+2)."""
    rows = (
        Like.manager.filter(task__task_group_id__in=[link.task_group_id for link in links])
        .values('task__task_group_id')
        .annotate(likes=Count('pk', filter=Q(value=1)), dislikes=Count('pk', filter=Q(value=-1)))
    )
    votes = {row['task__task_group_id']: (row['likes'], row['dislikes']) for row in rows}
    scores = {}
    for link in links:
        likes, dislikes = votes.get(link.task_group_id, (0, 0))
        scores[link.pk] = (likes + 1) / (likes + dislikes + 2)
    return scores


def salad_word_counts(links):
    """Answer words per Салатик placement; unknown sizes sort last."""
    from games.puzzles.salad.rules import parse_task_payload

    counts = {}
    tasks = Task.objects.filter(task_group_id__in=[link.task_group_id for link in links])
    by_group = {task.task_group_id: task for task in tasks}
    for link in links:
        task = by_group.get(link.task_group_id)
        try:
            counts[link.pk] = len(parse_task_payload(task.checker_data, task.answer)[1])
        except Exception:
            counts[link.pk] = 99
    return counts


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
    open_links = [link for link in candidates if link.task_group_id not in done]
    likes = like_scores(open_links)
    sizes = salad_word_counts(open_links) if game.id == 'salad' else {}
    ranked = []
    for order, link in enumerate(open_links):
        stars = (difficulties.get(link.pk) or {}).get('stars')
        if stars in preference:
            key = (preference.index(stars), sizes.get(link.pk, 0), -likes[link.pk], order)
            ranked.append((key, link))
    if not ranked:
        return None
    return min(ranked, key=lambda item: item[0])[1]
