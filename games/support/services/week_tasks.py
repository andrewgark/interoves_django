"""Support console: расписание «Задания недели» (порядок, даты, генерация)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.models import Game, GameTaskGroup
from games.week_task_pool import (
    materialize_unit,
    pick_random_units,
    scheduled_exclude_keys,
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
    if not ordered_links:
        return
    temp_base = 10_000
    for i, link in enumerate(ordered_links):
        new_num = i + 1
        link.number = str(temp_base + i)
        _sync_link_titles(link, new_num)
        link.save(update_fields=['number', 'name'])
    for i, link in enumerate(ordered_links):
        link.number = str(i + 1)
        link.save(update_fields=['number'])


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
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'number': number,
        'name': link.name,
        'publish_date': pub.date().isoformat() if pub else None,
        'source_label': _source_label(link),
        'source': src,
        'play_url': f'/games/{WEEK_TASK_GAME_ID}/{number}/',
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


@transaction.atomic
def update_week_task(link_id: int, *, name: str) -> dict[str, Any]:
    game = get_week_task_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WeekTaskSupportError('Задание недели не найдено')
    name = (name or '').strip()
    if not name:
        raise WeekTaskSupportError('Нужно название')
    if len(name) > 100:
        raise WeekTaskSupportError('Название слишком длинное (макс. 100)')
    link.name = name
    link.save(update_fields=['name'])
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
    now: datetime | None = None,
) -> dict[str, Any]:
    """Вставить случайное задание с публичным номером at_number; сдвинуть остальные."""
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

    exclude = scheduled_exclude_keys(week_task_game=game)
    units = pick_random_units(1, exclude=exclude)
    if not units:
        raise WeekTaskSupportError('В пуле не осталось заданий')
    link = materialize_unit(units[0], week_number=at_number, week_task_game=game)
    return get_week_task_detail(link.pk)


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
        'publish_start': get_publish_start_iso(),
        'week_task_count': len(rows),
        'published_count': published_count,
        'future_count': future_count,
        'today_number': today_number if any(r.is_today for r in rows) else None,
        'locked_until': last_published_number(now=now),
        'buffer_weeks': WEEK_TASK_BUFFER_WEEKS,
    }
