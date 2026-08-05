"""Общие операции с расписанием (GameTaskGroup слоты)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, TypeVar

from django.db import transaction

from games.models import Game, GameTaskGroup, Task, TaskGroup

RowT = TypeVar('RowT')


class ScheduleLinkError(Exception):
    """Ошибка удаления/перенумерации слота."""


def cascade_delete_link(link: GameTaskGroup) -> None:
    """Удалить связку GameTaskGroup → TaskGroup → Task."""
    tg_id = link.task_group_id
    link.delete()
    if not tg_id:
        return
    Task.objects.filter(task_group_id=tg_id).delete()
    TaskGroup.objects.filter(pk=tg_id).delete()


@transaction.atomic
def delete_future_slot(
    *,
    game: Game,
    link_id: int,
    is_number_published: Callable[[Game, int, datetime | None], bool],
    renumber_links: Callable[[list[GameTaskGroup]], None],
    list_rows: Callable[..., list[RowT]],
    error_cls: type[Exception] = ScheduleLinkError,
    not_found_msg: str = 'Слот не найден',
    published_msg: str = 'Нельзя удалять уже вышедшие',
    now: datetime | None = None,
) -> list[RowT]:
    """Удалить будущий слот и перенумеровать оставшиеся."""
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise error_cls(not_found_msg)
    try:
        number = int(link.number)
    except (TypeError, ValueError) as exc:
        raise error_cls('Некорректный номер слота') from exc
    if is_number_published(game, number, now):
        raise error_cls(published_msg.format(number=number))

    remaining = [
        row
        for row in GameTaskGroup.sorted_links(
            GameTaskGroup.objects.filter(game=game).select_related('task_group'),
            reverse=False,
        )
        if row.pk != link_id
    ]
    cascade_delete_link(link)
    if remaining:
        renumber_links(remaining)
    return list_rows(now=now)
