"""Support console: адвент-календарь (настройки раздела и расписание дней).

Каждый день — одна группа с одним заданием: HTML-условие и ответ со
стандартным чекером ``equals_with_possible_spaces`` (варианты по строкам).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.console.http.schedule import (
    assert_future_only_order,
    defer_future_slot,
    delete_future_slot,
    effective_schedule_number,
    renumber_links,
    restore_deferred_slot,
    save_publish_start_date,
    shift_links,
)
from games.daily.catalog.advent import (
    ADVENT_CHECKER_ID,
    ADVENT_DEFAULT_TITLE,
    ADVENT_END_DATE_TAG,
    ADVENT_GAME_ID,
    ADVENT_PUBLISH_START_TAG,
    ADVENT_SHOW_ANSWER_TAG,
    SECTION_DESCRIPTION_TAG,
    advent_day_count,
    advent_end_date,
    advent_publish_at,
    advent_publish_start,
    current_advent_number,
    is_advent_number_published,
)
from games.models import CheckerType, Game, GameTaskGroup, Task, TaskGroup
from games.sections.paths import section_hub_path, section_play_path

ADVENT_POINTS = 1
_AUTO_NAME_RE = re.compile(r'^День \d+$')


class AdventSupportError(Exception):
    """Ошибка операции с адвент-календарём."""


@dataclass(frozen=True)
class AdventRow:
    link_id: int
    task_group_id: int
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    is_after_end: bool
    answer: str
    play_url: str
    site_url: str
    is_deferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_advent_game() -> Game:
    try:
        return Game.objects.get(pk=ADVENT_GAME_ID)
    except Game.DoesNotExist as exc:
        raise AdventSupportError('Игра advent не найдена') from exc


def _auto_name(number: int) -> str:
    return f'День {number}'


def _sync_links(links: list[GameTaskGroup], new_numbers: list[int]) -> None:
    task_groups = []
    for link, new_num in zip(links, new_numbers):
        tg = link.task_group
        tg.label = f'advent:{new_num}'
        task_groups.append(tg)
        if not (link.name or '').strip() or _AUTO_NAME_RE.match(link.name):
            link.name = _auto_name(new_num)
    if task_groups:
        TaskGroup.objects.bulk_update(task_groups, ['label'])


def _renumber_links(ordered_links: list[GameTaskGroup]) -> None:
    renumber_links(ordered_links, sync_links=_sync_links)


def _shift_link_titles(link: GameTaskGroup, new_num: int) -> None:
    _sync_links([link], [new_num])


def _first_task(link: GameTaskGroup) -> Task | None:
    if not link.task_group_id:
        return None
    return (
        Task.objects.filter(task_group_id=link.task_group_id, is_removed=False)
        .order_by('pk')
        .first()
    )


def _site_url(link: GameTaskGroup, number: int) -> str:
    # The hash opens the day before its date, so it doubles as a preview link.
    if link.share_hash:
        return section_play_path(ADVENT_GAME_ID, link.share_hash)
    return section_play_path(ADVENT_GAME_ID, number)


def _publish_date(game: Game, number: int) -> Optional[str]:
    pub = advent_publish_at(game, number)
    return pub.date().isoformat() if pub else None


def _is_after_end(day_count: int | None, number: int) -> bool:
    return day_count is not None and number > day_count


def list_advent_rows(*, now: datetime | None = None) -> list[AdventRow]:
    game = get_advent_game()
    now = now or timezone.now()
    today_num = current_advent_number(game, now)
    day_count = advent_day_count(game)
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False, include_deferred=True,
    )
    answers = {}
    for tg_id, answer, checker_data in (
        Task.objects.filter(
            task_group_id__in=[link.task_group_id for link in links if link.task_group_id],
            is_removed=False,
        ).order_by('-pk').values_list('task_group_id', 'answer', 'checker_data')
    ):
        answers[tg_id] = (answer or checker_data or '').strip().split('\n')[0]
    rows: list[AdventRow] = []
    for link in links:
        number = effective_schedule_number(link)
        if number is None:
            continue
        is_pub = False if link.is_deferred else is_advent_number_published(game, number, now)
        rows.append(AdventRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            number=number,
            name=link.name or _auto_name(number),
            publish_date=_publish_date(game, number),
            is_published=is_pub,
            is_today=bool(today_num is not None and number == today_num and is_pub),
            is_after_end=not link.is_deferred and _is_after_end(day_count, number),
            answer=answers.get(link.task_group_id, ''),
            play_url=section_play_path(ADVENT_GAME_ID, number),
            site_url=_site_url(link, number),
            is_deferred=link.is_deferred,
        ))
    return rows


def _get_link(game: Game, link_id: int, *, lock: bool = False) -> GameTaskGroup:
    qs = GameTaskGroup.objects.filter(game=game, pk=link_id).select_related('task_group')
    if lock:
        qs = qs.select_for_update()
    link = qs.first()
    if link is None:
        raise AdventSupportError('День не найден')
    return link


def get_advent_detail(link_id: int) -> dict[str, Any]:
    game = get_advent_game()
    link = _get_link(game, link_id)
    number = effective_schedule_number(link) or 0
    task = _first_task(link)
    is_pub = False if link.is_deferred else is_advent_number_published(game, number)
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'number': number,
        'name': link.name or _auto_name(number),
        'publish_date': _publish_date(game, number),
        'is_published': is_pub,
        'is_deferred': link.is_deferred,
        'text': (task.text or '') if task else '',
        'answers': (task.checker_data or '') if task else '',
        'answer': (task.answer or '') if task else '',
        'answer_comment': (task.answer_comment or '') if task else '',
        'play_url': section_play_path(ADVENT_GAME_ID, number),
        'site_url': _site_url(link, number),
    }


def last_published_number(*, now: datetime | None = None) -> int:
    published = [r.number for r in list_advent_rows(now=now) if r.is_published]
    return max(published) if published else 0


def _clean_answers(raw) -> str:
    lines = [line.strip() for line in str(raw or '').replace('\r', '').split('\n')]
    return '\n'.join(line for line in lines if line)


def _clean_name(raw, number: int) -> str:
    name = str(raw or '').strip()[:200]
    return name or _auto_name(number)


@transaction.atomic
def create_advent_day(
    *,
    at_number: int,
    name: str | None = None,
    text: str = '',
    answers: str = '',
    answer: str = '',
    answer_comment: str = '',
    now: datetime | None = None,
) -> dict[str, Any]:
    if at_number < 1:
        raise AdventSupportError('Номер должен быть >= 1')
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise AdventSupportError(
            'Нельзя вставлять среди уже вышедших дней '
            '(доступно с №{})'.format(locked_until + 1)
        )
    game = Game.objects.select_for_update().get(pk=get_advent_game().pk)
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    max_num = max(
        (n for n in (effective_schedule_number(link) for link in links) if n is not None),
        default=0,
    )
    at_number = min(at_number, max_num + 1)

    to_shift = []
    for link in links:
        try:
            n = int(link.number)
        except (TypeError, ValueError):
            continue
        if n >= at_number:
            to_shift.append((n, link))
    to_shift.sort(key=lambda item: item[0], reverse=True)
    if to_shift:
        shift_links(
            [link for _n, link in to_shift],
            [n + 1 for n, _link in to_shift],
            sync_link=_shift_link_titles,
        )

    checker = CheckerType.objects.get(id=ADVENT_CHECKER_ID)
    answers = _clean_answers(answers)
    task_group = TaskGroup.objects.create(
        label=f'advent:{at_number}',
        checker=checker,
        points=ADVENT_POINTS,
        max_attempts=None,
    )
    Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='default',
        checker=checker,
        checker_data=answers,
        answer=(answer or '').strip() or (answers.split('\n')[0] if answers else ''),
        answer_comment=(answer_comment or '').strip() or None,
        text=text or '',
        tags={},
        points=ADVENT_POINTS,
        max_attempts=None,
        is_removed=False,
    )
    link = GameTaskGroup.objects.create(
        game=game,
        task_group=task_group,
        number=str(at_number),
        name=_clean_name(name, at_number),
    )
    return get_advent_detail(link.pk)


def _recheck_task_after_commit(task_id: int) -> None:
    def run():
        from games.ops.workers.jobs.recheck import recheck_full

        task = Task.objects.filter(pk=task_id).first()
        if task is not None:
            recheck_full(None, task=task)

    transaction.on_commit(run, robust=True)


@transaction.atomic
def update_advent_day(
    link_id: int,
    *,
    name: str | None = None,
    text: str | None = None,
    answers: str | None = None,
    answer: str | None = None,
    answer_comment: str | None = None,
) -> dict[str, Any]:
    """Править день. Новые варианты ответа перепроверяют уже отправленные."""
    game = get_advent_game()
    link = _get_link(game, link_id, lock=True)
    number = effective_schedule_number(link) or 0
    if name is not None:
        link.name = _clean_name(name, number)
        link.save(update_fields=['name'])
    task = _first_task(link)
    if task is None:
        raise AdventSupportError('У дня нет задания')
    answers_changed = False
    if text is not None:
        task.text = text
    if answers is not None:
        cleaned = _clean_answers(answers)
        answers_changed = cleaned != (task.checker_data or '')
        task.checker_data = cleaned
    if answer is not None:
        task.answer = answer.strip() or (task.checker_data or '').split('\n')[0]
    if answer_comment is not None:
        task.answer_comment = answer_comment.strip() or None
    task.save()
    if answers_changed:
        _recheck_task_after_commit(task.pk)
    return get_advent_detail(link.pk)


@transaction.atomic
def reorder_advent(ordered_link_ids: list[int], *, now: datetime | None = None) -> list[AdventRow]:
    game = get_advent_game()
    if not ordered_link_ids:
        raise AdventSupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise AdventSupportError('Дубликаты id в порядке')
    existing = list(
        GameTaskGroup.objects.select_for_update()
        .filter(game=game)
        .select_related('task_group')
    )
    active = [
        link for link in existing
        if not link.is_deferred and effective_schedule_number(link) is not None
    ]
    by_id = {link.pk: link for link in active}
    if set(ordered_link_ids) != set(by_id):
        raise AdventSupportError('Список дней устарел, обновите страницу и повторите')
    assert_future_only_order(
        ordered_link_ids,
        list_advent_rows(now=now),
        error_cls=AdventSupportError,
        published_msg='Нельзя менять порядок уже вышедших дней (№1–{number}). '
        'Переставляйте только будущие.',
    )
    _renumber_links([by_id[pk] for pk in ordered_link_ids])
    return list_advent_rows(now=now)


def delete_advent_day(link_id: int, *, now: datetime | None = None) -> list[AdventRow]:
    return delete_future_slot(
        game=get_advent_game(),
        link_id=link_id,
        is_number_published=is_advent_number_published,
        renumber_links=_renumber_links,
        list_rows=list_advent_rows,
        error_cls=AdventSupportError,
        not_found_msg='День не найден',
        published_msg='Нельзя удалять уже вышедший день №{number}',
        now=now,
    )


def defer_advent_day(link_id: int, *, now=None):
    return defer_future_slot(
        game=get_advent_game(), link_id=link_id,
        is_number_published=is_advent_number_published,
        renumber_links=_renumber_links, list_rows=list_advent_rows,
        error_cls=AdventSupportError, not_found_msg='День не найден',
        published_msg='Нельзя откладывать уже вышедший день №{number}', now=now,
    )


def restore_advent_day(link_id: int, *, now=None):
    return restore_deferred_slot(
        game=get_advent_game(), link_id=link_id,
        renumber_links=_renumber_links, list_rows=list_advent_rows,
        error_cls=AdventSupportError, not_found_msg='Отложенный день не найден', now=now,
    )


def get_advent_settings() -> dict[str, Any]:
    game = get_advent_game()
    start = advent_publish_start(game)
    end = advent_end_date(game)
    tags = game.tags or {}
    return {
        'title': game.outside_name or game.name or ADVENT_DEFAULT_TITLE,
        'description': str(tags.get(SECTION_DESCRIPTION_TAG) or ''),
        'start_date': start.date().isoformat() if start else '',
        'end_date': end.isoformat() if end else '',
        'day_count': advent_day_count(game),
        'show_answer': bool(tags.get(ADVENT_SHOW_ANSWER_TAG)),
        'hub_url': section_hub_path(ADVENT_GAME_ID),
    }


def _parse_date(raw, label: str) -> date | None:
    value = str(raw or '').strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise AdventSupportError(f'Некорректная дата: {label}') from exc


@transaction.atomic
def save_advent_settings(
    *,
    title: str,
    description: str,
    start_date: str,
    end_date: str = '',
    show_answer: bool = False,
) -> dict[str, Any]:
    """Сохранить название, описание и даты. Вышедшие дни не сдвигаются."""
    game = Game.objects.select_for_update().get(pk=get_advent_game().pk)
    title = str(title or '').strip()
    if not title:
        raise AdventSupportError('Нужно название')
    start = _parse_date(start_date, 'первый день')
    if start is None:
        raise AdventSupportError('Нужна дата первого дня')
    end = _parse_date(end_date, 'последний день')
    if end is not None and end < start:
        raise AdventSupportError('Последний день раньше первого')

    old_start = advent_publish_start(game)
    if old_start is not None and old_start.date() != start:
        now = timezone.now()
        published = [r.number for r in list_advent_rows(now=now) if r.is_published]
        if published:
            raise AdventSupportError(
                'Уже вышли дни №1–{}: дату первого дня менять нельзя'.format(max(published))
            )

    save_publish_start_date(
        game, ADVENT_PUBLISH_START_TAG, start.isoformat(), error_cls=AdventSupportError,
    )
    game.refresh_from_db(fields=['tags'])
    tags = dict(game.tags or {})
    if end is None:
        tags.pop(ADVENT_END_DATE_TAG, None)
    else:
        tags[ADVENT_END_DATE_TAG] = end.isoformat()
    description = str(description or '').strip()
    if description:
        tags[SECTION_DESCRIPTION_TAG] = description
    else:
        tags.pop(SECTION_DESCRIPTION_TAG, None)
    if show_answer:
        tags[ADVENT_SHOW_ANSWER_TAG] = True
    else:
        tags.pop(ADVENT_SHOW_ANSWER_TAG, None)
    game.tags = tags
    game.name = title
    game.outside_name = title
    game.save(update_fields=['tags', 'name', 'outside_name'])
    return get_advent_settings()


def advent_dashboard_context(*, now: datetime | None = None) -> dict[str, Any]:
    from games.console.http.schedule import build_schedule_page_context

    rows = list_advent_rows(now=now)
    settings = get_advent_settings()
    ctx = build_schedule_page_context(
        rows,
        title=settings['title'],
        prefix='advent',
        list_label='Дни адвент-календаря',
        publish_start=settings['start_date'] or None,
    )
    ctx.update({
        'rows': rows,
        'advent_json': [row.to_dict() for row in rows],
        'advent_settings': settings,
    })
    return ctx
