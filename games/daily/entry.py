"""Which daily puzzle to open for a newcomer (ads, /start/, hub experiment).

Newcomers whose first puzzle was today's one, Sep 21 - Nov 1, 2026 (n=794;
the first puzzle is close to random with respect to the person, so this is
nearly a natural experiment):

    stars  finished  returned within 7 days
    1★     84%       31%
    2★     77%       29%
    3★     58%       27%
    4★     62%       30%
    5★     42%       27%

Difficulty moves finishing a lot but not coming back (logit, game fixed
effects: every star level p > 0.4 for 7- and 14-day return). The share of
likes on the puzzle is the one placement trait tied to return (coef 1.07,
p = 0.03): ~24% return on the worst-liked third of puzzles, ~31% on the rest.

So the free window (the latest seven, never behind the Club paywall) is
ranked by the expected newcomer outcome:

    score = like share × newcomer finish rate

both Bayesian-smoothed: likes towards the game's usual share, the finish rate
of newcomers who began with that very puzzle towards the rate for its star
rating. Puzzles the visitor already finished, 5★ and unrated ones are never
chosen. A shorter Салатик breaks ties (Nov 3-6: newcomers who left a Салатик
unfinished had often found most words and stalled on the last ones).
"""

from django.core.cache import cache
from django.db.models import Count, Min, Q

from games.club.access.archive import FREE_ARCHIVE_COUNT
from games.daily.difficulty.core import get_cached_game_difficulties
from games.daily.registry import get_daily_game
from games.models import GameTaskGroup, Like, PlayerCompletedGame, PlayerStartedGame, Task

NEWCOMER_GAME_IDS = ('ladder', 'salad', 'alphabetty', 'censorly')
ELIGIBLE_STARS = (1, 2, 3, 4)
# Usual like share per game (all numbered puzzles, Nov 2026) and the weight,
# in votes, of that prior.
LIKE_PRIOR = {'salad': 0.63, 'ladder': 0.70, 'alphabetty': 0.68, 'censorly': 0.56}
DEFAULT_LIKE_PRIOR = 0.65
LIKE_PRIOR_VOTES = 10
# Newcomer finish rate by star rating (table above) and its weight, in newcomers.
FINISH_PRIOR = {1: 0.84, 2: 0.77, 3: 0.58, 4: 0.62}
FINISH_PRIOR_PLAYERS = 10
NEWCOMER_STATS_CACHE_SECONDS = 3600


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


def like_votes(links):
    """(likes, dislikes) per placement pk."""
    rows = (
        Like.manager.filter(task__task_group_id__in=[link.task_group_id for link in links])
        .values('task__task_group_id')
        .annotate(likes=Count('pk', filter=Q(value=1)), dislikes=Count('pk', filter=Q(value=-1)))
    )
    by_group = {row['task__task_group_id']: (row['likes'], row['dislikes']) for row in rows}
    return {link.pk: by_group.get(link.task_group_id, (0, 0)) for link in links}


def like_score(likes, dislikes, prior):
    return (likes + LIKE_PRIOR_VOTES * prior) / (likes + dislikes + LIKE_PRIOR_VOTES)


def finish_score(finished, newcomers, stars):
    return (finished + FINISH_PRIOR_PLAYERS * FINISH_PRIOR[stars]) / (newcomers + FINISH_PRIOR_PLAYERS)


def _compute_newcomer_finish_counts(task_group_ids):
    """{task_group_id: (newcomers, finished)} for players whose first game it was.

    A newcomer is a user or anonymous actor whose earliest trusted start is
    this placement; team rows are not people.
    """
    from games.ops.analytics.metrics import TRUSTED_IDENTITY_CUTOVER

    trusted = PlayerStartedGame.objects.filter(
        is_backfilled=False, started_at__gte=TRUSTED_IDENTITY_CUTOVER, team__isnull=True,
    )
    rows = list(
        trusted.filter(task_group_id__in=task_group_ids)
        .values_list('user_id', 'anon_key', 'task_group_id', 'started_at')
    )
    users = {user_id for user_id, _anon, _tg, _at in rows if user_id}
    anons = {anon for user_id, anon, _tg, _at in rows if not user_id and anon}
    earliest = {}
    if users:
        for user_id, first_at in (
            trusted.filter(user_id__in=users, anon_key__isnull=True)
            .values('user_id').annotate(first_at=Min('started_at')).values_list('user_id', 'first_at')
        ):
            earliest[('u', user_id)] = first_at
    if anons:
        for anon, first_at in (
            trusted.filter(anon_key__in=anons, user__isnull=True)
            .values('anon_key').annotate(first_at=Min('started_at')).values_list('anon_key', 'first_at')
        ):
            earliest[('a', anon)] = first_at
    finished = set()
    for user_id, anon, tg in PlayerCompletedGame.objects.filter(
        task_group_id__in=task_group_ids, team__isnull=True,
    ).values_list('user_id', 'anon_key', 'task_group_id'):
        finished.add((('u', user_id) if user_id else ('a', anon), tg))
    counts = {tg: [0, 0] for tg in task_group_ids}
    for user_id, anon, tg, started_at in rows:
        actor = ('u', user_id) if user_id else ('a', anon)
        if earliest.get(actor) != started_at:
            continue
        counts[tg][0] += 1
        if (actor, tg) in finished:
            counts[tg][1] += 1
    return {tg: tuple(value) for tg, value in counts.items()}


def newcomer_finish_counts(links):
    """Cached {placement pk: (newcomers, finished)}; recomputed hourly."""
    group_ids = sorted({link.task_group_id for link in links})
    if not group_ids:
        return {}
    key = 'daily-entry:newcomer-finish:v1:' + ','.join(map(str, group_ids))
    by_group = cache.get(key)
    if by_group is None:
        by_group = _compute_newcomer_finish_counts(group_ids)
        cache.set(key, by_group, NEWCOMER_STATS_CACHE_SECONDS)
    return {link.pk: by_group.get(link.task_group_id, (0, 0)) for link in links}


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
    """Best-scoring free puzzle the actor has not finished (see module docstring).

    None when no candidate has an eligible visible rating yet; callers fall
    back to the latest puzzle.
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
    rated = []
    for order, link in enumerate(candidates):
        stars = (difficulties.get(link.pk) or {}).get('stars')
        if stars in ELIGIBLE_STARS and link.task_group_id not in done:
            rated.append((order, link, stars))
    if not rated:
        return None
    links = [link for _order, link, _stars in rated]
    votes = like_votes(links)
    newcomers = newcomer_finish_counts(links)
    sizes = salad_word_counts(links) if game.id == 'salad' else {}
    prior = LIKE_PRIOR.get(game.id, DEFAULT_LIKE_PRIOR)

    def key(item):
        order, link, stars = item
        likes, dislikes = votes[link.pk]
        players, finished_count = newcomers[link.pk]
        score = like_score(likes, dislikes, prior) * finish_score(finished_count, players, stars)
        return (-round(score, 6), sizes.get(link.pk, 0), order)

    return min(rated, key=key)[1]
