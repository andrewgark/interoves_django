"""Support console: schedule + random generation for Цензурки."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
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
        number = int(link.number)
        task = _task_for_link(link)
        pub = censorly_publish_at(game, number)
        pub_date = pub.date().isoformat() if pub else None
        is_pub = is_censorly_number_published(game, number, now)
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
    return {
        'page_title': 'Цензурки',
        'hub_url': '/censorly/',
        'publish_start': start.date().isoformat() if start else '',
        'buffer_days': CENSORLY_BUFFER_DAYS,
        'schedule_rows': schedule,
        'schedule_json': [r.to_dict() for r in schedule],
        'random_rows': random_rows,
        'random_json': [r.to_dict() for r in random_rows],
        'schedule_count': len(schedule),
        'published_count': sum(1 for r in schedule if r.is_published),
        'future_count': sum(1 for r in schedule if not r.is_published),
    }


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
        if _is_numeric_number(link.number)
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
        if _is_numeric_number(link.number):
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


def reset_my_progress(*, user, share_hash: str = '', number: str = '') -> int:
    game = get_censorly_game()
    task = None
    if share_hash:
        row = RandomCensorlyGame.objects.filter(share_hash=share_hash).first()
        if row:
            task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    elif number:
        link = GameTaskGroup.objects.filter(game=game, number=str(number)).first()
        if link:
            task = Task.objects.filter(task_group_id=link.task_group_id, number='1').first()
    if task is None:
        raise CensorlySupportError('Партия не найдена')
    return reset_progress(game=game, task=task, user=user)
