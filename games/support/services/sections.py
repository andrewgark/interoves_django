from dataclasses import dataclass
from typing import List, Optional

from games.section_hub import SECTION_HUB_META, SECTION_HUB_ORDER, _newest_task_group_links


@dataclass(frozen=True)
class SectionRow:
    game_id: str
    icon: str
    title: str
    description: str
    latest_number: Optional[str]
    latest_name: Optional[str]
    task_group_count: int
    pending_count: int
    attempts_24h: int
    hint_requests_24h: int


def get_sections_dashboard() -> List[SectionRow]:
    from datetime import timedelta

    from django.utils import timezone

    from games.models import Attempt, Game, HintAttempt, Task

    since = timezone.now() - timedelta(hours=24)
    games = {
        g.id: g
        for g in Game.objects.filter(project_id='sections', id__in=SECTION_HUB_ORDER)
    }
    rows: List[SectionRow] = []
    for game_id in SECTION_HUB_ORDER:
        game = games.get(game_id)
        if game is None:
            continue
        meta = SECTION_HUB_META[game_id]
        links = list(_newest_task_group_links(game))
        latest = links[0] if links else None
        task_ids = list(
            Task.objects.filter(task_group__game_links__game_id=game_id).values_list('id', flat=True)
        )
        hint_qs = HintAttempt.objects.filter(time__gte=since, is_real_request=True)
        if task_ids:
            hint_qs = hint_qs.filter(hint__task_id__in=task_ids)
        else:
            hint_qs = hint_qs.none()
        rows.append(SectionRow(
            game_id=game_id,
            icon=meta['icon'],
            title=meta['title'],
            description=meta['description'],
            latest_number=latest.number if latest else None,
            latest_name=(latest.name or latest.task_group.label) if latest else None,
            task_group_count=len(links),
            pending_count=Attempt.manager.filter(game=game, status='Pending').count(),
            attempts_24h=Attempt.manager.filter(game=game, time__gte=since, skip=False).count(),
            hint_requests_24h=hint_qs.count(),
        ))
    return rows
