"""Support console: schedule + random generation for Цензурки."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from games.censorly import CENSORLY_CHECKER_ID, CENSORLY_GAME_ID, CENSORLY_TAGS_KEY, CENSORLY_TASK_TYPE
from games.censorly.play import CENSORLY_BASE_POINTS, puzzle_from_task, reset_progress
from games.censorly.random_game import (
    CensorlyPoolExhausted,
    EmptyCensorlyPool,
    create_from_title,
    get_or_create_random_game,
    puzzle_json_size,
    used_censorly_titles,
)
from games.censorly.tokenize import build_puzzle_payload, title_content_lemmas
from games.censorly.wiki import WikiFetchError, fetch_article
from games.censorly_daily import (
    CENSORLY_BUFFER_DAYS,
    CENSORLY_PUBLISH_START_TAG,
    MOSCOW,
    censorly_publish_at,
    censorly_publish_start,
    is_censorly_number_published,
)
from games.models import CheckerType, Game, GameTaskGroup, RandomCensorlyGame, Task, TaskGroup
from games.support.services.schedule_links import (
    defer_future_slot,
    effective_schedule_number,
    renumber_links,
    restore_deferred_slot,
    shift_links,
)


class CensorlySupportError(Exception):
    """Support operation failed."""


@dataclass(frozen=True)
class CensorlyScheduleRow:
    link_id: int
    task_group_id: int
    task_id: Optional[int]
    number: int
    name: str
    wiki_title: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    play_url: str
    token_count: int = 0
    is_deferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CensorlyRandomRow:
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


def _renumber_censorly_links(ordered_links: list[GameTaskGroup]) -> None:
    def sync_links(links, new_numbers):
        task_groups = []
        for link, new_number in zip(links, new_numbers):
            link.name = f'Цензурка #{new_number}'
            task_group = link.task_group
            if (task_group.label or '').startswith('censorly:'):
                task_group.label = f'censorly:{new_number}'
                task_groups.append(task_group)
        if task_groups:
            TaskGroup.objects.bulk_update(task_groups, ['label'])

    renumber_links(ordered_links, sync_links=sync_links)


def get_censorly_game() -> Game:
    try:
        return Game.objects.get(pk=CENSORLY_GAME_ID)
    except Game.DoesNotExist as exc:
        raise CensorlySupportError('Игра censorly не найдена — примените миграции') from exc


def _is_numeric_number(value) -> bool:
    try:
        int(str(value))
        return str(value).isdigit()
    except (TypeError, ValueError):
        return False


def _task_for_link(link: GameTaskGroup) -> Optional[Task]:
    return Task.objects.filter(task_group_id=link.task_group_id, number='1').first()


def _title_from_task(task: Optional[Task]) -> str:
    if task is None:
        return ''
    return (task.answer or '').strip().splitlines()[0] if task.answer else ''


def _token_count(task: Optional[Task]) -> int:
    payload = puzzle_from_task(task) if task else None
    if not payload:
        return 0
    return len(payload.get('title_tokens') or []) + len(payload.get('body_tokens') or [])


def list_schedule_rows(*, now: datetime | None = None) -> list[CensorlyScheduleRow]:
    game = get_censorly_game()
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    links = [
        link for link in GameTaskGroup.objects.filter(game=game).select_related('task_group')
        if _is_numeric_number(link.number)
    ]
    links.sort(key=lambda link: int(link.number))
    rows: list[CensorlyScheduleRow] = []
    for link in links:
        number = effective_schedule_number(link)
        if number is None:
            continue
        task = _task_for_link(link)
        pub = censorly_publish_at(game, number)
        pub_date = pub.astimezone(MOSCOW).date().isoformat() if pub else None
        is_pub = not link.is_deferred and is_censorly_number_published(game, number, now)
        rows.append(CensorlyScheduleRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            task_id=task.pk if task else None,
            number=number,
            name=link.name or f'Цензурка #{number}',
            wiki_title=_title_from_task(task),
            publish_date=pub_date,
            is_published=is_pub,
            is_today=bool(pub and pub.date() == today),
            play_url=f'/censorly/{number}/',
            token_count=_token_count(task),
            is_deferred=bool(link.is_deferred),
        ))
    return rows


def list_random_rows(*, limit: int = 50) -> list[CensorlyRandomRow]:
    rows: list[CensorlyRandomRow] = []
    for row in RandomCensorlyGame.objects.select_related('task_group').order_by('-created_at')[:limit]:
        task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
        rows.append(CensorlyRandomRow(
            id=row.pk,
            wiki_title=row.wiki_title,
            share_hash=row.share_hash,
            play_url=row.play_url(),
            created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
            task_id=task.pk if task else None,
            token_count=_token_count(task),
            payload_bytes=puzzle_json_size(task) if task else 0,
        ))
    return rows


def dashboard_context() -> dict[str, Any]:
    game = get_censorly_game()
    start = censorly_publish_start(game)
    schedule = list_schedule_rows()
    random_rows = list_random_rows()
    active_schedule = [row for row in schedule if not row.is_deferred]
    return {
        'page_title': 'Цензурки',
        'schedule_title': 'Цензурки',
        'schedule_prefix': 'cz',
        'schedule_list_label': 'Список Цензурок',
        'hub_url': '/censorly/',
        'publish_start': start.date().isoformat() if start else '',
        'buffer_days': CENSORLY_BUFFER_DAYS,
        'schedule_rows': schedule,
        'schedule_json': [r.to_dict() for r in schedule],
        'random_rows': random_rows,
        'random_json': [r.to_dict() for r in random_rows],
        'schedule_count': len(active_schedule),
        'published_count': sum(1 for r in active_schedule if r.is_published),
        'future_count': sum(1 for r in active_schedule if not r.is_published),
        'today_number': next((r.number for r in active_schedule if r.is_today), None),
        'deferred_count': len(schedule) - len(active_schedule),
    }


def defer_censorly(link_id: int, *, now=None):
    return defer_future_slot(
        game=get_censorly_game(), link_id=link_id,
        is_number_published=is_censorly_number_published,
        renumber_links=_renumber_censorly_links,
        list_rows=list_schedule_rows,
        error_cls=CensorlySupportError,
        not_found_msg='Цензурка не найдена',
        published_msg='Нельзя откладывать уже вышедшую Цензурку №{number}',
        now=now,
    )


def restore_censorly(link_id: int, *, now=None):
    return restore_deferred_slot(
        game=get_censorly_game(), link_id=link_id,
        renumber_links=_renumber_censorly_links,
        list_rows=list_schedule_rows,
        error_cls=CensorlySupportError,
        not_found_msg='Отложенная Цензурка не найдена',
        now=now,
    )


def set_publish_start(date_iso: str) -> str:
    try:
        d = date.fromisoformat(str(date_iso).strip()[:10])
    except ValueError as exc:
        raise CensorlySupportError('Некорректная дата publish_start') from exc
    game = get_censorly_game()
    tags = dict(game.tags or {})
    tags[CENSORLY_PUBLISH_START_TAG] = f'{d.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.save(update_fields=['tags'])
    return d.isoformat()


def _create_numbered_slot(*, number: int, article, pool_title: str | None = None) -> GameTaskGroup:
    game = get_censorly_game()
    puzzle = build_puzzle_payload(
        wiki_title=article.title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        wiki_revid=getattr(article, 'revid', None),
        fetched_at=timezone.now().isoformat(),
        truncated=bool(getattr(article, 'truncated', False)),
    )
    if pool_title and pool_title != article.title:
        puzzle['source_pool_title'] = pool_title
    if not title_content_lemmas(puzzle):
        raise CensorlySupportError('В названии нет угадываемых слов')
    checker = CheckerType.objects.get(pk=CENSORLY_CHECKER_ID)
    tg = TaskGroup.objects.create(
        label=f'censorly:{number}'[:100],
        checker=checker,
        points=CENSORLY_BASE_POINTS,
    )
    Task.objects.create(
        task_group=tg,
        number='1',
        task_type=CENSORLY_TASK_TYPE,
        checker=checker,
        answer=article.title,
        tags={CENSORLY_TAGS_KEY: puzzle},
        points=CENSORLY_BASE_POINTS,
        is_removed=False,
    )
    return GameTaskGroup.objects.create(
        game=game,
        task_group=tg,
        number=str(number),
        name=f'Цензурка #{number}',
    )


@transaction.atomic
def create_at_number(at_number: int, *, title_or_url: str | None = None) -> dict[str, Any]:
    if at_number < 1:
        raise CensorlySupportError('Номер должен быть >= 1')
    game = get_censorly_game()
    if is_censorly_number_published(game, at_number):
        raise CensorlySupportError(f'Нельзя вставлять в уже вышедший день №{at_number}')
    links = [
        link for link in GameTaskGroup.objects.filter(game=game)
        if not link.is_deferred and _is_numeric_number(link.number)
    ]
    to_shift = []
    for link in links:
        n = int(link.number)
        if n >= at_number:
            to_shift.append((n, link))
    if to_shift:
        planned = [(old, old + 1, link) for old, link in to_shift]
        def sync(link, new_num):
            link.name = f'Цензурка #{new_num}'
            tg = link.task_group
            if (tg.label or '').startswith('censorly:'):
                tg.label = f'censorly:{new_num}'
                tg.save(update_fields=['label'])
        shift_links(
            [link for _o, _n, link in planned],
            [new for _o, new, _l in planned],
            sync_link=sync,
        )
    if title_or_url:
        article = fetch_article(title_or_url)
        link = _create_numbered_slot(number=at_number, article=article)
    else:
        # Use pool; skip redirects to already-used articles.
        from games.censorly.article_pool import load_article_pool
        import random
        used = used_censorly_titles()
        pool = [t for t in load_article_pool() if t not in used]
        if not pool:
            raise CensorlySupportError('Пул статей исчерпан')
        random.shuffle(pool)
        article = None
        pool_title = None
        errors = []
        for title in pool[:12]:
            try:
                candidate = fetch_article(title)
            except WikiFetchError as exc:
                errors.append(str(exc))
                continue
            if candidate.title in used:
                used.add(title)
                continue
            article = candidate
            pool_title = title
            break
        if article is None:
            raise CensorlySupportError('Не удалось загрузить статью: ' + '; '.join(errors[:2]))
        link = _create_numbered_slot(number=at_number, article=article, pool_title=pool_title)
    rows = list_schedule_rows()
    return {'link_id': link.pk, 'number': at_number, 'wiki_title': article.title, 'rows': [r.to_dict() for r in rows]}


@transaction.atomic
def generate_more(n: int = 5) -> dict[str, Any]:
    if n < 1 or n > 60:
        raise CensorlySupportError('N должно быть от 1 до 60')
    game = get_censorly_game()
    max_num = 0
    for link in GameTaskGroup.objects.filter(game=game):
        if not link.is_deferred and _is_numeric_number(link.number):
            max_num = max(max_num, int(link.number))
    created = []
    for i in range(n):
        detail = create_at_number(max_num + 1 + i, title_or_url=None)
        created.append({'number': detail['number'], 'wiki_title': detail['wiki_title']})
    return {
        'created_count': len(created),
        'created': created,
        'rows': [r.to_dict() for r in list_schedule_rows()],
    }


def generate_random() -> CensorlyRandomRow:
    try:
        row = get_or_create_random_game()
    except (EmptyCensorlyPool, CensorlyPoolExhausted, WikiFetchError) as exc:
        raise CensorlySupportError(str(exc)) from exc
    task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    return CensorlyRandomRow(
        id=row.pk,
        wiki_title=row.wiki_title,
        share_hash=row.share_hash,
        play_url=row.play_url(),
        created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
        task_id=task.pk if task else None,
        token_count=_token_count(task),
        payload_bytes=puzzle_json_size(task) if task else 0,
    )


def generate_from_title(title_or_url: str) -> CensorlyRandomRow:
    try:
        row = create_from_title(title_or_url)
    except WikiFetchError as exc:
        raise CensorlySupportError(str(exc)) from exc
    task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    return CensorlyRandomRow(
        id=row.pk,
        wiki_title=row.wiki_title,
        share_hash=row.share_hash,
        play_url=row.play_url(),
        created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
        task_id=task.pk if task else None,
        token_count=_token_count(task),
        payload_bytes=puzzle_json_size(task) if task else 0,
    )


def _task_for_party(*, share_hash: str = '', number: str = '') -> Task:
    game = get_censorly_game()
    task = None
    if share_hash:
        row = RandomCensorlyGame.objects.filter(share_hash=share_hash).first()
        if row:
            task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    elif number:
        link = GameTaskGroup.objects.filter(game=game).filter(
            Q(number=str(number)) | Q(deferred_number=str(number))
        ).first()
        if link:
            task = Task.objects.filter(task_group_id=link.task_group_id, number='1').first()
    if task is None:
        raise CensorlySupportError('Партия не найдена')
    return task


def reset_my_progress(*, user, share_hash: str = '', number: str = '') -> int:
    game = get_censorly_game()
    task = _task_for_party(share_hash=share_hash, number=number)
    return reset_progress(game=game, task=task, user=user)


def refetch_article_text(*, share_hash: str = '', number: str = '') -> dict[str, Any]:
    """Replace the frozen Wikipedia body. Attempts, title, and points stay."""
    task = _task_for_party(share_hash=share_hash, number=number)
    tags = task.tags if isinstance(task.tags, dict) else {}
    old = tags.get(CENSORLY_TAGS_KEY) if isinstance(tags.get(CENSORLY_TAGS_KEY), dict) else {}
    title = (old.get('wiki_title') or task.answer or '').strip()
    if not title:
        raise CensorlySupportError('У партии нет заголовка статьи')
    try:
        article = fetch_article(title)
    except WikiFetchError as exc:
        raise CensorlySupportError(str(exc)) from exc
    puzzle = build_puzzle_payload(
        wiki_title=title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        wiki_revid=article.revid,
        fetched_at=timezone.now().isoformat(),
        truncated=bool(article.truncated),
    )
    alias = (old.get('source_pool_title') or '').strip()
    if alias:
        puzzle['source_pool_title'] = alias
    if not title_content_lemmas(puzzle):
        raise CensorlySupportError('В названии нет угадываемых слов')
    new_tags = dict(tags)
    new_tags[CENSORLY_TAGS_KEY] = puzzle
    new_revision = uuid.uuid4()
    updated = Task.objects.filter(pk=task.pk, tags=tags).update(
        tags=new_tags,
        attempt_revision=new_revision,
    )
    if not updated:
        raise CensorlySupportError('Партия изменилась во время скачивания; обновите страницу и повторите')
    task.tags = new_tags
    task.attempt_revision = new_revision
    return {
        'wiki_title': title,
        'token_count': len(puzzle.get('title_tokens') or []) + len(puzzle.get('body_tokens') or []),
        'truncated': bool(puzzle.get('truncated')),
    }
