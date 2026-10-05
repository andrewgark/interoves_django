"""Общие операции с расписанием (GameTaskGroup слоты)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, TypeVar

from django.db import transaction

from games.models import Game, GameTaskGroup, Task, TaskGroup

RowT = TypeVar('RowT')


class ScheduleLinkError(Exception):
    """Ошибка удаления/перенумерации слота."""


def effective_schedule_number(link: GameTaskGroup) -> int | None:
    """Return the public number, including for a deferred schedule slot."""
    raw = link.deferred_number if link.is_deferred else link.number
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
    scheduled_rows = [row for row in rows if not getattr(row, 'is_deferred', False)]
    published_count = sum(
        1 for row in scheduled_rows if getattr(row, 'is_published', False)
    )
    if today_number is None:
        today_number = next(
            (
                getattr(row, 'number')
                for row in scheduled_rows
                if getattr(row, 'is_today', False)
            ),
            None,
        )
    return {
        'schedule_title': title,
        'schedule_prefix': prefix,
        'schedule_list_label': list_label,
        'schedule_count': len(scheduled_rows),
        'publish_start': publish_start,
        'published_count': published_count,
        'future_count': len(scheduled_rows) - published_count,
        'deferred_count': len(rows) - len(scheduled_rows),
        'today_number': today_number,
    }


@transaction.atomic
def defer_future_slot(*, game, link_id, is_number_published, renumber_links,
                      list_rows, error_cls, not_found_msg, published_msg,
                      now=None):
    link = GameTaskGroup.objects.filter(game=game, pk=link_id).select_related('task_group').first()
    if link is None:
        raise error_cls(not_found_msg)
    try:
        number = int(link.number)
    except (TypeError, ValueError) as exc:
        raise error_cls('Некорректный номер слота') from exc
    if is_number_published(game, number, now):
        raise error_cls(published_msg.format(number=number))
    link.deferred_number = str(link.number)
    # Free the old numeric slot before renumbering the remaining links.
    link.number = str(max(
        [int(item.number) for item in GameTaskGroup.objects.filter(game=game)
         if str(item.number).lstrip('-').isdigit()], default=0
    ) + 10000)
    link.is_deferred = True
    link.save(update_fields=['is_deferred', 'deferred_number', 'number'])
    active = list(GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game, is_deferred=False).select_related('task_group'),
    ))
    if active:
        renumber_links(active)
    return list_rows(now=now)


@transaction.atomic
def restore_deferred_slot(*, game, link_id, renumber_links, list_rows, error_cls,
                          not_found_msg, now=None):
    link = GameTaskGroup.objects.filter(game=game, pk=link_id).select_related('task_group').first()
    if link is None or not link.is_deferred:
        raise error_cls(not_found_msg)
    link.is_deferred = False
    link.save(update_fields=['is_deferred'])
    active = list(GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game, is_deferred=False).select_related('task_group'),
    ))
    renumber_links(active)
    return list_rows(now=now)


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


def _free_number_base(occupied: set[str], count: int) -> int:
    """Первый диапазон из ``count`` номеров, которого нет среди занятых."""
    base = 10_000
    span = count + 10_000
    while any(str(base + offset) in occupied for offset in range(count)):
        base += span
    return base


def _occupied_numbers(game_ids: set) -> set[str]:
    return {
        str(number)
        for number in GameTaskGroup.objects.filter(
            game_id__in=game_ids,
        ).values_list('number', flat=True)
    }


def _park_rows_holding(links: list[GameTaskGroup], reserved_numbers: set[str]) -> None:
    """Увести чужие слоты той же игры с номеров, которые займёт эта операция.

    Отложенные выпуски остаются в игре и держат unique(game, number). Если их
    номер попадает в диапазон активного расписания, запись активных слотов
    падает с Duplicate entry — так было с салатиками №75–81.
    """
    if not links or not reserved_numbers:
        return
    ids_by_game: dict = {}
    for link in links:
        ids_by_game.setdefault(link.game_id, set()).add(link.pk)
    for game_id, link_ids in ids_by_game.items():
        blockers = list(GameTaskGroup.objects.select_for_update().filter(
            game_id=game_id,
            number__in=reserved_numbers,
        ).exclude(pk__in=link_ids))
        if not blockers:
            continue
        blockers.sort(key=lambda row: GameTaskGroup.try_number_key(row.number) or ())
        occupied = _occupied_numbers({game_id})
        base = _free_number_base(occupied, len(blockers))
        for offset, blocker in enumerate(blockers):
            blocker.number = str(base + offset)
        GameTaskGroup.objects.bulk_update(blockers, ['number'])


def _assign_numbers_without_collision(
    links: list[GameTaskGroup],
    new_numbers: list[int],
    *,
    sync_link: Callable[[GameTaskGroup, int], None] | None,
    sync_links: Callable[[list[GameTaskGroup], list[int]], None] | None,
) -> None:
    _park_rows_holding(links, {str(number) for number in new_numbers})
    temp_base = _free_number_base(_occupied_numbers({link.game_id for link in links}), len(links))
    for offset, link in enumerate(links):
        link.number = str(temp_base + offset)
    if sync_links is not None:
        sync_links(links, new_numbers)
    elif sync_link is not None:
        for link, new_num in zip(links, new_numbers):
            sync_link(link, new_num)
    GameTaskGroup.objects.bulk_update(links, ['number', 'name'])
    for link, new_num in zip(links, new_numbers):
        link.number = str(new_num)
    # All target numbers are free now: changed links were parked above and
    # deferred blockers were parked by _park_rows_holding.  The final bulk
    # UPDATE is therefore safe even though MySQL checks the unique index while
    # applying each row of a multi-row UPDATE.
    GameTaskGroup.objects.bulk_update(links, ['number'])


def renumber_links(
    ordered_links: list[GameTaskGroup],
    *,
    sync_link: Callable[[GameTaskGroup, int], None] | None = None,
    sync_links: Callable[[list[GameTaskGroup], list[int]], None] | None = None,
) -> None:
    """Двухфазно выставить номера 1..N, не меняя стабильные link id.

    Первая фаза уводит все номера во временный свободный диапазон, чтобы не
    нарушить unique(game, number). ``sync_link`` синхронизирует доменные
    названия/labels с будущим публичным номером.
    """
    if sync_link is not None and sync_links is not None:
        raise ValueError('Укажите только sync_link или sync_links')
    if not ordered_links:
        return
    new_numbers = [index + 1 for index in range(len(ordered_links))]
    old_names = {link.pk: link.name for link in ordered_links}
    if sync_links is not None:
        sync_links(ordered_links, new_numbers)
    elif sync_link is not None:
        for link, new_number in zip(ordered_links, new_numbers):
            sync_link(link, new_number)
    changed = [
        (link, new_number)
        for link, new_number in zip(ordered_links, new_numbers)
        if str(link.number) != str(new_number)
        or link.name != old_names[link.pk]
    ]
    if not changed:
        return
    changed_links = [link for link, _new_number in changed]
    changed_numbers = [new_number for _link, new_number in changed]
    _assign_numbers_without_collision(
        changed_links,
        changed_numbers,
        sync_link=None,
        sync_links=None,
    )


def shift_links(
    links: list[GameTaskGroup],
    new_numbers: list[int],
    *,
    sync_link: Callable[[GameTaskGroup, int], None] | None = None,
    sync_links: Callable[[list[GameTaskGroup], list[int]], None] | None = None,
) -> None:
    """Пакетно сдвинуть номера выбранных ссылок с сохранением unique-ограничения."""
    if len(links) != len(new_numbers):
        raise ValueError('Количество ссылок и новых номеров не совпадает')
    if sync_link is not None and sync_links is not None:
        raise ValueError('Укажите только sync_link или sync_links')
    if not links:
        return
    _assign_numbers_without_collision(
        links,
        new_numbers,
        sync_link=sync_link,
        sync_links=sync_links,
    )


def cascade_delete_link(link: GameTaskGroup) -> None:
    """Удалить связку GameTaskGroup → TaskGroup → Task.

    Если TaskGroup — предложение лесенки (LadderOffer) или салатика
    (WordSaladOffer), удаляем только слот расписания: Task/посылки/лайки
    и сам offer сохраняем.
    """
    tg_id = link.task_group_id
    link.delete()
    if not tg_id:
        return
    from games.models import LadderOffer, WordSaladOffer
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
    salad_offer = WordSaladOffer.objects.filter(task_group_id=tg_id).first()
    if salad_offer is not None:
        salad_offer.accepted_link = None
        if salad_offer.status == WordSaladOffer.STATUS_ACCEPTED:
            salad_offer.status = WordSaladOffer.STATUS_SENT
            if not salad_offer.sent_at:
                from django.utils import timezone
                salad_offer.sent_at = timezone.now()
        salad_offer.save(update_fields=['accepted_link', 'status', 'sent_at', 'updated_at'])
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
