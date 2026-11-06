"""The concrete line for a regular's Club offer: what the archive holds for them.

The offer used to list the Club's features; 24 regulars saw it on Nov 2–6
and none followed it. A regular who has solved many editions of a game and
has at least as many unsolved ones locked in its archive gets that game named
with both numbers instead: «Вы решили 18 Салатиков — в архиве ещё 56».
"""

from dataclasses import dataclass

from django.core.cache import cache

from games.club.access.archive import FREE_ARCHIVE_COUNT, is_club_archive_game
from games.daily.registry import get_daily_game
from games.models import Game, GameTaskGroup, PlayerCompletedGame

# Daily games with a paid archive, and «решили N …» / «ещё N …» word forms.
PITCH_GAMES = (
    ('salad', 'Салатик,Салатика,Салатиков'),
    ('ladder', 'Лесенку,Лесенки,Лесенок'),
    ('alphabetty', 'Алфавитку,Алфавитки,Алфавиток'),
)
MIN_SOLVED = 5
ARCHIVE_CACHE_SECONDS = 60 * 60


@dataclass(frozen=True)
class ArchivePitch:
    game_id: str
    forms: str
    solved: int
    unsolved_in_archive: int


def archive_task_group_ids(game_id):
    """Task groups of a game's published editions behind the paywall (cached)."""
    key = 'club-archive-task-groups:{}'.format(game_id)
    cached = cache.get(key)
    if cached is not None:
        return cached
    definition = get_daily_game(game_id)
    game = Game.objects.filter(id=game_id).first()
    if definition is None or game is None or not is_club_archive_game(game_id):
        return []
    links = GameTaskGroup.objects.filter(game=game, is_deferred=False)
    published = [
        link for link in definition.schedule.filter_published(links, game)
        if str(link.number).isdigit()
    ]
    published.sort(key=lambda link: int(link.number), reverse=True)
    ids = [link.task_group_id for link in published[FREE_ARCHIVE_COUNT:]]
    cache.set(key, ids, ARCHIVE_CACHE_SECONDS)
    return ids


def archive_pitch(user):
    """The game where ``user`` solved the most, if its archive holds at least as many more."""
    best = None
    for game_id, forms in PITCH_GAMES:
        solved_groups = set(
            PlayerCompletedGame.objects.filter(user=user, game_id=game_id)
            .values_list('task_group_id', flat=True)
        )
        solved = len(solved_groups)
        if solved < MIN_SOLVED:
            continue
        archive = archive_task_group_ids(game_id)
        unsolved = len([tg for tg in archive if tg not in solved_groups])
        if unsolved < solved:
            continue
        if best is None or solved > best.solved:
            best = ArchivePitch(game_id, forms, solved, unsolved)
    return best
