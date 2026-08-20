"""Support console: расписание «Задания недели» (порядок, даты, генерация)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.models import Game, GameTaskGroup
from games.support.services.banned import (
    add_banned_unit,
    list_banned_units,
    remove_banned_unit,
)
from games.support.services.schedule_links import delete_future_slot, renumber_links
from games.week_task_pool import (
    WEEK_TASK_SOURCE_TAG,
    materialize_unit,
    pick_random_units,
    pool_catalog,
    rematerialize_link,
    resolve_unit,
    scheduled_exclude_keys,
    source_play_path_from_tags,
    source_summary_from_tags,
)
from games.week_task_weekly import (
    WEEK_TASK_BUFFER_WEEKS,
    WEEK_TASK_GAME_ID,
    WEEK_TASK_PUBLISH_START_TAG,
    current_week_task_number,
    is_week_task_number_published,
    week_task_publish_at,
    week_task_publish_start,
)

logger = logging.getLogger(__name__)


class WeekTaskSupportError(Exception):
    """Ошибка операции с заданиями недели."""


@dataclass(frozen=True)
class WeekTaskRow:
    link_id: int
    task_group_id: int
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    source_label: str
    play_url: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_week_task_game() -> Game:
    try:
        return Game.objects.get(pk=WEEK_TASK_GAME_ID)
    except Game.DoesNotExist as exc:
        raise WeekTaskSupportError('Игра week_task не найдена') from exc


def _lock_week_task_game() -> Game:
    try:
        return Game.objects.select_for_update().get(pk=WEEK_TASK_GAME_ID)
    except Game.DoesNotExist as exc:
        raise WeekTaskSupportError('Игра week_task не найдена') from exc


def _sync_link_titles(link: GameTaskGroup, new_num: int) -> None:
    tg = link.task_group
    if (tg.label or '').startswith('week_task:') or not (tg.label or '').strip():
        tg.label = f'week_task:{new_num}'
        tg.save(update_fields=['label'])


def _renumber_links(ordered_links: list[GameTaskGroup]) -> None:
    renumber_links(ordered_links, sync_link=_sync_link_titles)


def _source_label(link: GameTaskGroup) -> str:
    tags = (link.task_group.tags or {}) if link.task_group_id else {}
    src = source_summary_from_tags(tags)
    parts = []
    des = src.get('desyatka_label') or src.get('game_id')
    if des:
        parts.append(str(des))
    major = src.get('major')
    if major is not None:
        parts.append(f'п.{major}')
    return ' · '.join(parts) if parts else '—'


def list_week_task_rows(*, now: datetime | None = None) -> list[WeekTaskRow]:
    game = get_week_task_game()
    now = now or timezone.now()
    today_num = current_week_task_number(game, now)
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    rows: list[WeekTaskRow] = []
    for link in links:
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            continue
        pub = week_task_publish_at(game, number)
        pub_date = pub.date().isoformat() if pub else None
        is_pub = is_week_task_number_published(game, number, now)
        is_today = bool(today_num is not None and number == today_num and is_pub)
        tags = (link.task_group.tags or {}) if link.task_group_id else {}
        rows.append(WeekTaskRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            number=number,
            name=link.name or f'Задание недели #{number}',
            publish_date=pub_date,
            is_published=is_pub,
            is_today=is_today,
            source_label=_source_label(link),
            play_url=f'/games/{WEEK_TASK_GAME_ID}/{number}/',
            source_url=source_play_path_from_tags(tags) or '',
        ))
    return rows


def get_week_task_detail(link_id: int) -> dict[str, Any]:
    game = get_week_task_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WeekTaskSupportError('Задание недели не найдено')
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    pub = week_task_publish_at(game, number)
    tags = (link.task_group.tags or {}) if link.task_group_id else {}
    src = source_summary_from_tags(tags)
    is_pub = is_week_task_number_published(game, number)
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'number': number,
        'name': link.name,
        'publish_date': pub.date().isoformat() if pub else None,
        'is_published': is_pub,
        'source_label': _source_label(link),
        'source': src,
        'source_task_group_id': src.get('task_group_id'),
        'source_major': src.get('major'),
        'source_task_numbers': src.get('task_numbers'),
        'play_url': f'/games/{WEEK_TASK_GAME_ID}/{number}/',
        'source_url': source_play_path_from_tags(tags) or '',
    }


def get_publish_start_iso() -> Optional[str]:
    start = week_task_publish_start(get_week_task_game())
    if start is None:
        return None
    return start.date().isoformat()


@transaction.atomic
def set_publish_start(date_iso: str) -> str:
    try:
        d = date.fromisoformat(str(date_iso).strip()[:10])
    except ValueError as exc:
        raise WeekTaskSupportError('Некорректная дата publish_start') from exc
    if d.weekday() != 0:
        raise WeekTaskSupportError('Дата №1 должна быть понедельником')
    game = get_week_task_game()
    tags = dict(game.tags or {})
    tags[WEEK_TASK_PUBLISH_START_TAG] = f'{d.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.save(update_fields=['tags'])
    return d.isoformat()


def last_published_number(*, now: datetime | None = None) -> int:
    published = [r.number for r in list_week_task_rows(now=now) if r.is_published]
    return max(published) if published else 0


def _assert_future_only_order(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> None:
    current = list_week_task_rows(now=now)
    locked = [r for r in current if r.is_published]
    if not locked:
        return
    locked_ids = [r.link_id for r in locked]
    if ordered_link_ids[: len(locked_ids)] != locked_ids:
        last = locked[-1].number
        raise WeekTaskSupportError(
            'Нельзя менять порядок уже вышедших заданий (№1–{}). '
            'Переставляйте только будущие.'.format(last)
        )


@transaction.atomic
def reorder_week_tasks(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[WeekTaskRow]:
    game = get_week_task_game()
    if not ordered_link_ids:
        raise WeekTaskSupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise WeekTaskSupportError('Дубликаты id в порядке')

    existing = list(
        GameTaskGroup.objects.filter(game=game).select_related('task_group')
    )
    by_id = {link.pk: link for link in existing}
    if set(ordered_link_ids) != set(by_id):
        raise WeekTaskSupportError(
            'Список id не совпадает с текущими заданиями '
            '(обновите страницу и повторите)'
        )
    _assert_future_only_order(ordered_link_ids, now=now)
    ordered = [by_id[pk] for pk in ordered_link_ids]
    _renumber_links(ordered)
    return list_week_task_rows(now=now)


def _resolve_unit_from_payload(
    *,
    source_task_group_id: int | None,
    major: str | None = None,
    task_numbers: list[str] | None = None,
):
    if source_task_group_id is None:
        return None
    try:
        tg_id = int(source_task_group_id)
    except (TypeError, ValueError) as exc:
        raise WeekTaskSupportError('Некорректный source_task_group_id') from exc
    nums = None
    if task_numbers is not None:
        if not isinstance(task_numbers, (list, tuple)):
            raise WeekTaskSupportError('task_numbers должен быть списком')
        nums = [str(x) for x in task_numbers]
    try:
        return resolve_unit(
            source_task_group_id=tg_id,
            major=None if major is None or major == '' else str(major),
            task_numbers=nums,
        )
    except ValueError as exc:
        raise WeekTaskSupportError(str(exc)) from exc


@transaction.atomic
def update_week_task(
    link_id: int,
    *,
    name: str | None = None,
    source_task_group_id: int | None = None,
    major: str | None = None,
    task_numbers: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    game = get_week_task_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WeekTaskSupportError('Задание недели не найдено')

    unit = _resolve_unit_from_payload(
        source_task_group_id=source_task_group_id,
        major=major,
        task_numbers=task_numbers,
    )
    if unit is not None:
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            number = 0
        if is_week_task_number_published(game, number, now or timezone.now()):
            raise WeekTaskSupportError(
                'Нельзя менять источник уже вышедшего задания'
            )
        try:
            rematerialize_link(link, unit)
        except ValueError as exc:
            raise WeekTaskSupportError(str(exc)) from exc
        link.refresh_from_db()

    if name is not None:
        name = (name or '').strip()
        if not name:
            raise WeekTaskSupportError('Нужно название')
        if len(name) > 100:
            raise WeekTaskSupportError('Название слишком длинное (макс. 100)')
        link.name = name
        link.save(update_fields=['name'])
    elif unit is None:
        raise WeekTaskSupportError('Нужно name или источник')

    return get_week_task_detail(link.pk)


def _max_number(game: Game) -> int:
    max_num = 0
    for link in GameTaskGroup.objects.filter(game=game):
        try:
            max_num = max(max_num, int(link.number))
        except (TypeError, ValueError):
            pass
    return max_num


@transaction.atomic
def generate_more(n: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Дописать в конец N новых недель со случайными заданиями."""
    if n < 1:
        raise WeekTaskSupportError('N должно быть >= 1')
    if n > 52:
        raise WeekTaskSupportError('N слишком большое (макс. 52)')
    game = _lock_week_task_game()
    exclude = scheduled_exclude_keys(week_task_game=game)
    units = pick_random_units(n, exclude=exclude)
    if len(units) < n:
        raise WeekTaskSupportError(
            'В пуле осталось только {} заданий (нужно {})'.format(len(units), n)
        )
    max_num = _max_number(game)
    created = []
    for i, unit in enumerate(units):
        link = materialize_unit(unit, week_number=max_num + 1 + i, week_task_game=game)
        created.append(get_week_task_detail(link.pk))
    return {
        'created_count': len(created),
        'created': created,
        'rows': [r.to_dict() for r in list_week_task_rows(now=now)],
    }


@transaction.atomic
def create_week_task(
    *,
    at_number: int,
    source_task_group_id: int | None = None,
    major: str | None = None,
    task_numbers: list[str] | None = None,
    name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Вставить задание с публичным номером at_number; сдвинуть остальные.

    Без source_* — случайный unit из пула; иначе конкретный круг / подмножество.
    """
    if at_number < 1:
        raise WeekTaskSupportError('Номер должен быть >= 1')
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise WeekTaskSupportError(
            'Нельзя вставлять среди уже вышедших заданий '
            '(доступно с №{})'.format(locked_until + 1)
        )
    game = _lock_week_task_game()
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    max_num = _max_number(game)
    if at_number > max_num + 1:
        at_number = max_num + 1

    to_shift = []
    for link in links:
        try:
            n = int(link.number)
        except (TypeError, ValueError):
            continue
        if n >= at_number:
            to_shift.append((n, link))
    to_shift.sort(key=lambda x: x[0], reverse=True)
    if to_shift:
        planned = [(old, old + 1, link) for old, link in to_shift]
        temp_base = 10_000
        for i, (old, new, link) in enumerate(planned):
            link.number = str(temp_base + i)
            _sync_link_titles(link, new)
            link.save(update_fields=['number', 'name'])
        for old, new, link in planned:
            link.number = str(new)
            link.save(update_fields=['number'])

    unit = _resolve_unit_from_payload(
        source_task_group_id=source_task_group_id,
        major=major,
        task_numbers=task_numbers,
    )
    if unit is None:
        exclude = scheduled_exclude_keys(week_task_game=game)
        units = pick_random_units(1, exclude=exclude)
        if not units:
            raise WeekTaskSupportError('В пуле не осталось заданий')
        unit = units[0]
    try:
        link = materialize_unit(unit, week_number=at_number, week_task_game=game)
    except ValueError as exc:
        raise WeekTaskSupportError(str(exc)) from exc

    if name is not None:
        name = (name or '').strip()
        if name:
            if len(name) > 100:
                raise WeekTaskSupportError('Название слишком длинное (макс. 100)')
            link.name = name
            link.save(update_fields=['name'])
    return get_week_task_detail(link.pk)


def get_pool_catalog() -> list[dict[str, Any]]:
    game = get_week_task_game()
    exclude = scheduled_exclude_keys(week_task_game=game)
    return pool_catalog(exclude=exclude)


@transaction.atomic
def ensure_future_buffer(
    target: int = WEEK_TASK_BUFFER_WEEKS,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Держать буфер на target недель вперёд от текущего календарного номера.

    До старта: не меньше ``target`` слотов всего (все ещё будущие).
    После старта: ``max(number) >= current_week + target``.
    """
    now = now or timezone.now()
    game = _lock_week_task_game()
    rows = list_week_task_rows(now=now)
    max_num = max((r.number for r in rows), default=0)
    current = current_week_task_number(game, now)
    if current is None:
        need = max(0, target - max_num)
    else:
        need = max(0, current + target - max_num)
    if need == 0:
        future = sum(1 for r in rows if not r.is_published)
        return {'added': 0, 'future': future, 'target': target}
    try:
        result = generate_more(need, now=now)
        added = result['created_count']
    except WeekTaskSupportError as exc:
        logger.warning('week_task ensure_future_buffer: %s', exc)
        future = sum(1 for r in list_week_task_rows(now=now) if not r.is_published)
        return {'added': 0, 'future': future, 'target': target, 'error': str(exc)}
    except Exception as exc:
        logger.warning('week_task ensure_future_buffer race: %s', exc)
        future = sum(1 for r in list_week_task_rows(now=now) if not r.is_published)
        return {'added': 0, 'future': future, 'target': target, 'error': str(exc)}
    future = sum(1 for r in list_week_task_rows(now=now) if not r.is_published)
    logger.info('week_task ensure_future_buffer: added %s (target %s)', added, target)
    return {'added': added, 'future': future, 'target': target}


def _source_exclude_key(link: GameTaskGroup) -> tuple[int, frozenset[str] | None] | None:
    tags = (link.task_group.tags or {}) if link.task_group_id else {}
    src = tags.get(WEEK_TASK_SOURCE_TAG) or {}
    if not isinstance(src, dict):
        return None
    tg_id = src.get('task_group_id')
    if tg_id is None:
        return None
    try:
        tg_id_int = int(tg_id)
    except (TypeError, ValueError):
        return None
    nums = src.get('task_numbers')
    if nums is None:
        return (tg_id_int, None)
    return (tg_id_int, frozenset(str(x) for x in nums))


@transaction.atomic
def delete_week_task(link_id: int, *, now: datetime | None = None) -> list[WeekTaskRow]:
    """Удалить будущее задание недели (источник может снова попасть в генерацию)."""
    game = get_week_task_game()
    return delete_future_slot(
        game=game,
        link_id=link_id,
        is_number_published=is_week_task_number_published,
        renumber_links=_renumber_links,
        list_rows=list_week_task_rows,
        error_cls=WeekTaskSupportError,
        not_found_msg='Задание недели не найдено',
        published_msg='Нельзя удалять уже вышедшее задание №{number}',
        now=now,
    )


@transaction.atomic
def forbid_week_task(link_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Удалить будущее задание и запретить его источник для генерации."""
    game = get_week_task_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WeekTaskSupportError('Задание недели не найдено')
    exclude_key = _source_exclude_key(link)
    label = _source_label(link)
    rows = delete_week_task(link_id, now=now)
    banned = list_banned_units(game)
    if exclude_key is not None:
        nums = list(exclude_key[1]) if exclude_key[1] is not None else None
        banned = add_banned_unit(
            game,
            task_group_id=exclude_key[0],
            task_numbers=nums,
            label=label,
        )
    return {
        'rows': [r.to_dict() for r in rows],
        'banned': banned,
    }


def unban_week_task_unit(
    *,
    task_group_id: int,
    task_numbers: list[str] | None,
) -> list[dict[str, Any]]:
    game = get_week_task_game()
    return remove_banned_unit(
        game,
        task_group_id=task_group_id,
        task_numbers=task_numbers,
    )


def week_task_dashboard_context(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    rows = list_week_task_rows(now=now)
    game = get_week_task_game()
    today_number = current_week_task_number(game, now)
    published_count = sum(1 for r in rows if r.is_published)
    future_count = len(rows) - published_count
    return {
        'rows': rows,
        'week_tasks_json': [r.to_dict() for r in rows],
        'banned_json': list_banned_units(game),
        'publish_start': get_publish_start_iso(),
        'week_task_count': len(rows),
        'published_count': published_count,
        'future_count': future_count,
        'today_number': today_number if any(r.is_today for r in rows) else None,
        'locked_until': last_published_number(now=now),
        'buffer_weeks': WEEK_TASK_BUFFER_WEEKS,
    }
