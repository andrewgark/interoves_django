from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional

from django.utils import timezone

PROJECT_LABELS = {
    'main': 'Десяточки',
    'sections': 'Разделы',
    'glowbyte': 'Glowbyte',
}


@dataclass(frozen=True)
class ProjectGamesGroup:
    project_id: str
    project_label: str
    games: tuple


def project_label(project_id: str) -> str:
    return PROJECT_LABELS.get(project_id, project_id)


def group_games_by_project(games) -> List[ProjectGamesGroup]:
    by_project = OrderedDict()
    sorted_games = sorted(
        games,
        key=lambda game: (game.start_time or timezone.now(), game.name or game.id),
        reverse=True,
    )
    for game in sorted_games:
        pid = game.project_id
        by_project.setdefault(pid, []).append(game)
    return [
        ProjectGamesGroup(
            project_id=pid,
            project_label=project_label(pid),
            games=tuple(gs),
        )
        for pid, gs in by_project.items()
    ]


def games_for_actor(*, team=None, user=None, anon_key=None) -> List[ProjectGamesGroup]:
    from games.models import Game
    from games.support.services.feed import distinct_game_ids_for_actor

    game_ids = distinct_game_ids_for_actor(team=team, user=user, anon_key=anon_key)
    games = list(
        Game.objects.filter(id__in=game_ids)
        .select_related('project')
    )
    return group_games_by_project(games)


def get_all_games_by_project(*, project_id: Optional[str] = None) -> List[ProjectGamesGroup]:
    from games.models import Game

    qs = Game.objects.select_related('project').order_by('-start_time')
    if project_id:
        qs = qs.filter(project_id=project_id)
    return group_games_by_project(qs)
