from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import List, Optional

from django.db.models import Max
from django.utils import timezone

from games.models import Attempt, Game, Registration, Task


@dataclass(frozen=True)
class StuckTeamRow:
    team_name: str
    team_label: str
    last_attempt_time: Optional[object]
    actor_url: str


def get_stuck_teams(game: Game, *, minutes: int = 30) -> List[StuckTeamRow]:
    from django.urls import reverse

    cutoff = timezone.now() - timedelta(minutes=minutes)
    task_ids = list(
        Task.objects.filter(task_group__game_links__game=game, is_removed=False)
        .values_list('pk', flat=True)
        .distinct()
    )
    if not task_ids:
        return []

    recent_ok_teams = set(
        Attempt.manager.filter(
            game=game,
            task_id__in=task_ids,
            status='Ok',
            time__gte=cutoff,
        )
        .exclude(team__isnull=True)
        .values_list('team_id', flat=True)
    )

    rows: List[StuckTeamRow] = []
    for reg in Registration.objects.filter(game=game).select_related('team'):
        team = reg.team
        if team is None or team.is_hidden or team.is_tester:
            continue
        if team.name in recent_ok_teams:
            continue
        last_attempt = (
            Attempt.manager.filter(game=game, team=team)
            .aggregate(last=Max('time'))['last']
        )
        rows.append(StuckTeamRow(
            team_name=team.name,
            team_label=team.visible_name or team.name,
            last_attempt_time=last_attempt,
            actor_url=reverse('support:actor_team', kwargs={'team_name': team.name}),
        ))

    rows.sort(key=lambda row: row.last_attempt_time or datetime.min.replace(tzinfo=dt_timezone.utc))
    return rows
