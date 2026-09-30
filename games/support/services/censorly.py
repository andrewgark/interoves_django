"""Support console: generate and list Цензурки."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from django.utils import timezone

from games.censorly import CENSORLY_GAME_ID
from games.censorly.play import puzzle_from_task, reset_progress
from games.censorly.random_game import (
    CensorlyPoolExhausted,
    EmptyCensorlyPool,
    create_from_title,
    get_or_create_random_game,
    puzzle_json_size,
)
from games.censorly.wiki import WikiFetchError
from games.models import Game, RandomCensorlyGame, Task


class CensorlySupportError(Exception):
    """Support operation failed."""


@dataclass(frozen=True)
class CensorlyRow:
    id: int
    wiki_title: str
    share_hash: str
    play_url: str
    created_at: str
    task_id: Optional[int]
    token_count: int
    payload_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_censorly_game() -> Game:
    try:
        return Game.objects.get(pk=CENSORLY_GAME_ID)
    except Game.DoesNotExist as exc:
        raise CensorlySupportError('Игра censorly не найдена — примените миграции') from exc


def _task_for_row(row: RandomCensorlyGame) -> Optional[Task]:
    return Task.objects.filter(task_group_id=row.task_group_id, number='1').first()


def list_censorly_rows(*, limit: int = 100) -> list[CensorlyRow]:
    get_censorly_game()
    rows: list[CensorlyRow] = []
    qs = RandomCensorlyGame.objects.select_related('task_group').order_by('-created_at')[:limit]
    for row in qs:
        task = _task_for_row(row)
        payload = puzzle_from_task(task) if task else None
        token_count = 0
        if payload:
            token_count = len(payload.get('title_tokens') or []) + len(payload.get('body_tokens') or [])
        rows.append(CensorlyRow(
            id=row.pk,
            wiki_title=row.wiki_title,
            share_hash=row.share_hash,
            play_url=row.play_url(),
            created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
            task_id=task.pk if task else None,
            token_count=token_count,
            payload_bytes=puzzle_json_size(task) if task else 0,
        ))
    return rows


def dashboard_context() -> dict[str, Any]:
    rows = list_censorly_rows()
    return {
        'rows': rows,
        'rows_json': [r.to_dict() for r in rows],
        'page_title': 'Цензурки',
        'hub_url': '/censorly/',
    }


def generate_random() -> CensorlyRow:
    try:
        row = get_or_create_random_game()
    except EmptyCensorlyPool as exc:
        raise CensorlySupportError(str(exc)) from exc
    except CensorlyPoolExhausted as exc:
        raise CensorlySupportError(str(exc)) from exc
    except WikiFetchError as exc:
        raise CensorlySupportError(str(exc)) from exc
    task = _task_for_row(row)
    payload = puzzle_from_task(task) if task else None
    token_count = 0
    if payload:
        token_count = len(payload.get('title_tokens') or []) + len(payload.get('body_tokens') or [])
    return CensorlyRow(
        id=row.pk,
        wiki_title=row.wiki_title,
        share_hash=row.share_hash,
        play_url=row.play_url(),
        created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
        task_id=task.pk if task else None,
        token_count=token_count,
        payload_bytes=puzzle_json_size(task) if task else 0,
    )


def generate_from_title(title_or_url: str) -> CensorlyRow:
    try:
        row = create_from_title(title_or_url)
    except WikiFetchError as exc:
        raise CensorlySupportError(str(exc)) from exc
    except Exception as exc:
        raise CensorlySupportError(str(exc)) from exc
    task = _task_for_row(row)
    payload = puzzle_from_task(task) if task else None
    token_count = 0
    if payload:
        token_count = len(payload.get('title_tokens') or []) + len(payload.get('body_tokens') or [])
    return CensorlyRow(
        id=row.pk,
        wiki_title=row.wiki_title,
        share_hash=row.share_hash,
        play_url=row.play_url(),
        created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
        task_id=task.pk if task else None,
        token_count=token_count,
        payload_bytes=puzzle_json_size(task) if task else 0,
    )


def reset_my_progress(*, user, share_hash: str) -> int:
    game = get_censorly_game()
    row = RandomCensorlyGame.objects.filter(share_hash=share_hash).first()
    if row is None:
        raise CensorlySupportError('Партия не найдена')
    task = _task_for_row(row)
    if task is None:
        raise CensorlySupportError('Задание не найдено')
    return reset_progress(game=game, task=task, user=user)
