"""Общие операции с расписанием (GameTaskGroup слоты)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, TypeVar

from django.db import transaction

from games.models import Game, GameTaskGroup, Task, TaskGroup

RowT = TypeVar('RowT')


class ScheduleLinkError(Exception):
    """Ошибка удаления/перенумерации слота."""


def build_schedule_page_context(
    rows: list[RowT],
    *,
    title: str,
    prefix: str,
    list_label: str,
    publish_start: str | None,
    today_number: int | None = None,
) -> dict:
    """Нормализованный контекст общего шаблона ежедневного расписания."""
    published_count = sum(
        1 for row in rows if getattr(row, 'is_published', False)
    )
    if today_number is None:
        today_number = next(
            (
                getattr(row, 'number')
                for row in rows
                if getattr(row, 'is_today', False)
            ),
            None,
        )
    return {
        'schedule_title': title,
        'schedule_prefix': prefix,
        'schedule_list_label': list_label,
        'schedule_count': len(rows),
        'publish_start': publish_start,
        'published_count': published_count,
        'future_count': len(rows) - published_count,
        'today_number': today_number,
    }


def assert_future_only_order(
    ordered_link_ids: list[int],
    rows: list[RowT],
    *,
    error_cls: type[Exception] = ScheduleLinkError,
    published_msg: str = 'Нельзя менять порядок уже вышедших выпусков (№1–{number}). '
    'Переставляйте только будущие.',
) -> None:
    """Зафиксировать опубликованный префикс общего ежедневного расписания."""
    locked = [row for row in rows if getattr(row, 'is_published', False)]
    if not locked:
        return
    locked_ids = [getattr(row, 'link_id') for row in locked]
    if ordered_link_ids[:len(locked_ids)] != locked_ids:
        raise error_cls(published_msg.format(number=getattr(locked[-1], 'number')))


def renumber_links(
    ordered_links: list[GameTaskGroup],
    *,
    sync_link: Callable[[GameTaskGroup, int], None] | None = None,
) -> None:
    """Двухфазно выставить номера 1..N, не меняя стабильные link id.

    Первая фаза уводит все номера во временный свободный диапазон, чтобы не
    нарушить unique(game, number). ``sync_link`` синхронизирует доменные
    названия/labels с будущим публичным номером.
    """
    if not ordered_links:
        return
    occupied = {str(link.number) for link in ordered_links}
    temp_base = 10_000
    while any(str(temp_base + i) in occupied for i in range(len(ordered_links))):
        temp_base += len(ordered_links) + 10_000
    for i, link in enumerate(ordered_links):
        new_num = i + 1
        link.number = str(temp_base + i)
        if sync_link is not None:
            sync_link(link, new_num)
        link.save(update_fields=['number', 'name'])
    for i, link in enumerate(ordered_links):
        link.number = str(i + 1)
        link.save(update_fields=['number'])


def cascade_delete_link(link: GameTaskGroup) -> None:
    """Удалить связку GameTaskGroup → TaskGroup → Task.

    Если TaskGroup — предложение лесенки (LadderOffer), удаляем только слот
    расписания: Task/посылки/лайки и сам offer сохраняем.
    """
    tg_id = link.task_group_id
    link.delete()
    if not tg_id:
        return
    from games.models import LadderOffer
    offer = LadderOffer.objects.filter(task_group_id=tg_id).first()
    if offer is not None:
        # Отвязать от расписания, вернуть в «отправлена» для повторного accept.
        offer.accepted_link = None
        if offer.status == LadderOffer.STATUS_ACCEPTED:
            offer.status = LadderOffer.STATUS_SENT
            if not offer.sent_at:
                from django.utils import timezone
                offer.sent_at = timezone.now()
        offer.save(update_fields=['accepted_link', 'status', 'sent_at', 'updated_at'])
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
