from dataclasses import dataclass
from typing import List, Optional

from django.urls import reverse

from games.section_hub import SECTION_HUB_META, SECTION_HUB_ORDER, _newest_task_group_links
from games.support.services.word_salad import (
    WORD_SALAD_GAME_ID,
    WORD_SALAD_SECTION_ICON,
    WORD_SALAD_SECTION_TITLE,
    ensure_word_salad_game,
    list_word_salad_rows,
)


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
    site_url: str


def get_sections_dashboard() -> List[SectionRow]:
    from datetime import timedelta

    from django.utils import timezone

    from games.models import Attempt, Game, HintAttempt, Task
    from games.section_paths import section_hub_path

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
            site_url=section_hub_path(game_id),
        ))
    word_salad_game = ensure_word_salad_game()
    word_salad_rows = list_word_salad_rows()
    task_ids = list(
        Task.objects.filter(task_group__game_links__game_id=WORD_SALAD_GAME_ID).values_list('id', flat=True)
    )
    hint_qs = HintAttempt.objects.filter(time__gte=since, is_real_request=True)
    if task_ids:
        hint_qs = hint_qs.filter(hint__task_id__in=task_ids)
    else:
        hint_qs = hint_qs.none()
    rows.append(SectionRow(
        game_id=WORD_SALAD_GAME_ID,
        icon=WORD_SALAD_SECTION_ICON,
        title=WORD_SALAD_SECTION_TITLE,
        description='Скрытый support-раздел для сборки и проверки Word Salad.',
        latest_number=word_salad_rows[-1].number if word_salad_rows else None,
        latest_name=word_salad_rows[-1].name if word_salad_rows else None,
        task_group_count=len(word_salad_rows),
        pending_count=0,
        attempts_24h=Attempt.manager.filter(game=word_salad_game, time__gte=since, skip=False).count(),
        hint_requests_24h=hint_qs.count(),
        site_url=reverse('support:word_salad'),
    ))
    return rows
