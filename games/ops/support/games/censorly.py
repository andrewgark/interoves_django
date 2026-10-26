"""Support console: schedule + random generation for Цензурки."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional
import copy
import uuid

from django.db import transaction
from django.utils import timezone

from games.puzzles.censorly.ids import (
    CENSORLY_CHECKER_ID,
    CENSORLY_GAME_ID,
    CENSORLY_SPLITTER_VERSION,
    CENSORLY_TAGS_KEY,
    CENSORLY_TASK_TYPE,
)
from games.puzzles.censorly.play import CENSORLY_BASE_POINTS, puzzle_from_task, reset_progress
from games.puzzles.censorly.random_game import (
    CensorlyPoolExhausted,
    EmptyCensorlyPool,
    create_from_title,
    get_or_create_random_game,
    puzzle_json_size,
    used_censorly_titles,
)
from games.puzzles.censorly.text.tokenize import build_puzzle_payload, title_content_lemmas
from games.puzzles.censorly.article.wiki import WikiArticle, WikiFetchError, fetch_article
from games.daily.catalog.censorly import (
    CENSORLY_BUFFER_DAYS,
    CENSORLY_PUBLISH_START_TAG,
    MOSCOW,
    censorly_publish_at,
    censorly_publish_start,
    is_censorly_number_published,
)
from games.models import CensorlyOffer, CheckerType, Game, GameTaskGroup, RandomCensorlyGame, Task, TaskGroup
from games.support.services.schedule_links import (
    build_schedule_page_context,
    defer_future_slot,
    effective_schedule_number,
    renumber_links,
    restore_deferred_slot,
    save_publish_start_date,
    shift_links,
)


class CensorlySupportError(Exception):
    """Support operation failed."""


@dataclass(frozen=True)
class CensorlyScheduleRow:
    link_id: int
    task_group_id: int
    number: int
    name: str
    wiki_title: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    play_url: str
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


def _random_row_token_count(task: Optional[Task]) -> int:
    """Read current random-game tokens from the already-loaded JSON payload."""
    if task is not None and isinstance(task.tags, dict):
        payload = task.tags.get(CENSORLY_TAGS_KEY)
        if (
            isinstance(payload, dict)
            and payload.get('splitter_version') == CENSORLY_SPLITTER_VERSION
            and isinstance(payload.get('title_tokens'), list)
            and isinstance(payload.get('body_tokens'), list)
        ):
            return len(payload['title_tokens']) + len(payload['body_tokens'])
    return _token_count(task)


def list_schedule_rows(*, now: datetime | None = None) -> list[CensorlyScheduleRow]:
    game = get_censorly_game()
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    links = [
        link for link in GameTaskGroup.objects.filter(game=game).select_related('task_group')
        if _is_numeric_number(link.number)
    ]
    links.sort(key=lambda link: int(link.number))
    tasks_by_group = {
        task.task_group_id: task
        for task in Task.objects.filter(
            task_group_id__in=[link.task_group_id for link in links], number='1'
        ).only('id', 'task_group_id', 'answer')
    }
    rows: list[CensorlyScheduleRow] = []
    for link in links:
        number = effective_schedule_number(link)
        if number is None:
            continue
        task = tasks_by_group.get(link.task_group_id)
        pub = censorly_publish_at(game, number)
        pub_date = pub.astimezone(MOSCOW).date().isoformat() if pub else None
        is_pub = not link.is_deferred and is_censorly_number_published(game, number, now)
        rows.append(CensorlyScheduleRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            number=number,
            name=link.name or f'Цензурка #{number}',
            wiki_title=_title_from_task(task),
            publish_date=pub_date,
            is_published=is_pub,
            is_today=bool(not link.is_deferred and pub and pub.date() == today),
            play_url=f'/censorly/{number}/',
            is_deferred=bool(link.is_deferred),
        ))
    return rows


def list_random_rows(*, limit: int = 50) -> list[CensorlyRandomRow]:
    random_games = list(
        RandomCensorlyGame.objects.order_by('-created_at')[:limit]
    )
    tasks_by_group = {}
    if random_games:
        tasks = Task.objects.filter(
            task_group_id__in=[row.task_group_id for row in random_games], number='1',
        ).order_by('pk').only(
            'id', 'task_group_id', 'tags', 'checker_data', 'attempt_revision',
        )
        for task in tasks:
            tasks_by_group.setdefault(task.task_group_id, task)
    rows: list[CensorlyRandomRow] = []
    for row in random_games:
        task = tasks_by_group.get(row.task_group_id)
        rows.append(CensorlyRandomRow(
            id=row.pk,
            wiki_title=row.wiki_title,
            share_hash=row.share_hash,
            play_url=row.play_url(),
            created_at=timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M') if row.created_at else '',
            task_id=task.pk if task else None,
            token_count=_random_row_token_count(task),
            payload_bytes=puzzle_json_size(task) if task else 0,
        ))
    return rows


def dashboard_context() -> dict[str, Any]:
    game = get_censorly_game()
    start = censorly_publish_start(game)
    schedule = list_schedule_rows()
    return {
        'page_title': 'Цензурки',
        **build_schedule_page_context(
            schedule,
            title='Цензурки',
            prefix='cz',
            list_label='Список Цензурок',
            publish_start=start.date().isoformat() if start else '',
        ),
        'hub_url': '/censorly/',
        'buffer_days': CENSORLY_BUFFER_DAYS,
        'schedule_rows': schedule,
        'schedule_json': [r.to_dict() for r in schedule],
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


def list_censorly_offers(*, limit: int = 100) -> list[dict[str, Any]]:
    offers = CensorlyOffer.objects.select_related('user', 'user__profile', 'party', 'accepted_link')[:limit]
    rows = []
    for offer in offers:
        profile = getattr(offer.user, 'profile', None)
        user_name = ' '.join(filter(None, [
            (getattr(profile, 'first_name', '') or '').strip(),
            (getattr(profile, 'last_name', '') or '').strip(),
        ])) or offer.user.username
        rows.append({
            'id': offer.pk,
            'wiki_title': offer.party.wiki_title,
            'share_hash': offer.party.share_hash,
            'play_url': offer.party.play_url(),
            'status': offer.status,
            'status_label': offer.status_label,
            'comment': offer.comment,
            'admin_note': offer.admin_note,
            'created_at': timezone.localtime(offer.created_at).strftime('%Y-%m-%d %H:%M'),
            'user_name': user_name,
            'telegram_handle': (getattr(profile, 'telegram_handle', '') or '').strip(),
            'accepted_number': offer.accepted_link.number if offer.accepted_link_id else None,
        })
    return rows


@transaction.atomic
def accept_censorly_offer(offer_id: int) -> CensorlyOffer:
    offer = CensorlyOffer.objects.select_for_update().select_related('party').filter(pk=offer_id).first()
    if offer is None:
        raise CensorlySupportError('Предложение не найдено')
    if offer.status != CensorlyOffer.STATUS_SENT:
        raise CensorlySupportError('Предложение уже обработано')
    game = Game.objects.select_for_update().get(pk=CENSORLY_GAME_ID)
    active_numbers = [
        int(value) for value in GameTaskGroup.objects.filter(game=game, is_deferred=False)
        .values_list('number', flat=True) if _is_numeric_number(value)
    ]
    number = max(active_numbers, default=0) + 1
    source_group = offer.party.task_group
    daily_group = copy.copy(source_group)
    daily_group.pk = None
    daily_group.label = f'censorly:{number}'[:100]
    daily_group.save()
    if source_group.authors.exists():
        daily_group.authors.set(source_group.authors.all())
    link = GameTaskGroup.objects.create(
        game=game,
        task_group=daily_group,
        number=str(number),
        name=f'Цензурка #{number}',
    )
    source_tasks = list(Task.objects.filter(task_group=source_group).order_by('number'))
    for source_task in source_tasks:
        daily_task = copy.copy(source_task)
        daily_task.pk = None
        daily_task._state.adding = True
        daily_task.task_group = daily_group
        daily_task.attempt_revision = uuid.uuid4()
        daily_task.save()
    offer.status = CensorlyOffer.STATUS_ACCEPTED
    offer.accepted_link = link
    offer.accepted_at = timezone.now()
    offer.save(update_fields=['status', 'accepted_link', 'accepted_at', 'updated_at'])
    return offer


@transaction.atomic
def reject_censorly_offer(offer_id: int, *, note: str = '') -> CensorlyOffer:
    offer = CensorlyOffer.objects.select_for_update().filter(pk=offer_id).first()
    if offer is None:
        raise CensorlySupportError('Предложение не найдено')
    if offer.status != CensorlyOffer.STATUS_SENT:
        raise CensorlySupportError('Предложение уже обработано')
    offer.status = CensorlyOffer.STATUS_REJECTED
    offer.admin_note = (note or '').strip()[:4000]
    offer.save(update_fields=['status', 'admin_note', 'updated_at'])
    return offer


@transaction.atomic
def reorder_censorly(ordered_link_ids: list[int], *, now=None) -> list[CensorlyScheduleRow]:
    game = get_censorly_game()
    if not ordered_link_ids:
        raise CensorlySupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise CensorlySupportError('Дубликаты id в порядке')
    links = list(
        GameTaskGroup.objects.select_for_update()
        .filter(game=game)
        .select_related('task_group')
    )
    active = [link for link in links if not link.is_deferred and _is_numeric_number(link.number)]
    by_id = {link.pk: link for link in active}
    if set(ordered_link_ids) != set(by_id):
        raise CensorlySupportError('Список id не совпадает с текущими Цензурками (обновите страницу и повторите)')
    from games.support.services.schedule_links import assert_future_only_order
    assert_future_only_order(
        ordered_link_ids,
        list_schedule_rows(now=now),
        error_cls=CensorlySupportError,
        published_msg='Нельзя менять порядок уже вышедших Цензурок (№1–{number}). Переставляйте только будущие.',
    )
    _renumber_censorly_links([by_id[pk] for pk in ordered_link_ids])
    return list_schedule_rows(now=now)


def set_publish_start(date_iso: str) -> str:
    return save_publish_start_date(
        get_censorly_game(), CENSORLY_PUBLISH_START_TAG, date_iso,
        error_cls=CensorlySupportError,
    )


def _create_numbered_slot(*, number: int, article: WikiArticle, pool_title: str | None = None) -> GameTaskGroup:
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


def _pick_pool_article(used: set[str]) -> tuple[WikiArticle, str]:
    from games.puzzles.censorly.article.admin import active_pool_titles
    import random

    pool = [title for title in active_pool_titles() if title not in used]
    if not pool:
        raise CensorlySupportError('Пул статей исчерпан')
    random.shuffle(pool)
    errors = []
    # Pool generation is synchronous in the support console. Keep the fallback
    # scan short and use a tighter timeout than explicit article creation.
    for title in pool[:3]:
        try:
            article = fetch_article(title, timeout=8)
        except WikiFetchError as exc:
            errors.append(str(exc))
            continue
        if article.title in used:
            used.add(title)
            continue
        used.add(article.title)
        used.add(title)
        return article, title
    detail = '; '.join(errors[:2]) or 'не найдено свободной статьи'
    raise CensorlySupportError('Не удалось загрузить статью: ' + detail)


def _shift_censorly_schedule_from(at_number: int, game: Game) -> None:
    links = [
        link for link in GameTaskGroup.objects.filter(game=game)
        if not link.is_deferred and _is_numeric_number(link.number)
    ]
    to_shift = [(int(link.number), link) for link in links if int(link.number) >= at_number]
    if not to_shift:
        return
    planned = [(old, old + 1, link) for old, link in to_shift]

    def sync(link, new_num):
        link.name = f'Цензурка #{new_num}'
        tg = link.task_group
        if (tg.label or '').startswith('censorly:'):
            tg.label = f'censorly:{new_num}'
            tg.save(update_fields=['label'])

    shift_links(
        [link for _old, _new, link in planned],
        [new for _old, new, _link in planned],
        sync_link=sync,
    )


def create_at_number(at_number: int, *, title_or_url: str | None = None) -> dict[str, Any]:
    if at_number < 1:
        raise CensorlySupportError('Номер должен быть >= 1')
    if is_censorly_number_published(get_censorly_game(), at_number):
        raise CensorlySupportError(f'Нельзя вставлять в уже вышедший день №{at_number}')
    pool_title = None
    if title_or_url:
        try:
            article = fetch_article(title_or_url)
        except WikiFetchError as exc:
            raise CensorlySupportError(str(exc)) from exc
    else:
        article, pool_title = _pick_pool_article(used_censorly_titles())

    with transaction.atomic():
        game = get_censorly_game()
        if is_censorly_number_published(game, at_number):
            raise CensorlySupportError(f'Нельзя вставлять в уже вышедший день №{at_number}')
        _shift_censorly_schedule_from(at_number, game)
        link = _create_numbered_slot(number=at_number, article=article, pool_title=pool_title)
        rows = list_schedule_rows()
    return {'link_id': link.pk, 'number': at_number, 'wiki_title': article.title, 'rows': [r.to_dict() for r in rows]}


def generate_more(n: int = 5) -> dict[str, Any]:
    if n < 1 or n > 10:
        raise CensorlySupportError('N должно быть от 1 до 10')
    game = get_censorly_game()
    existing_numbers = [
        int(link.number) for link in GameTaskGroup.objects.filter(game=game, is_deferred=False)
        if _is_numeric_number(link.number)
    ]
    initial_max = max(existing_numbers, default=0)
    for number in range(initial_max + 1, initial_max + n + 1):
        if is_censorly_number_published(game, number):
            raise CensorlySupportError(f'Нельзя добавлять статью в уже вышедший день №{number}')

    used = used_censorly_titles()
    articles = [_pick_pool_article(used) for _ in range(n)]
    created = []
    with transaction.atomic():
        game = get_censorly_game()
        max_num = max((
            int(link.number) for link in GameTaskGroup.objects.filter(game=game, is_deferred=False)
            if _is_numeric_number(link.number)
        ), default=0)
        for offset, (article, pool_title) in enumerate(articles, start=1):
            number = max_num + offset
            if is_censorly_number_published(game, number):
                raise CensorlySupportError(f'Нельзя добавлять статью в уже вышедший день №{number}')
            _create_numbered_slot(number=number, article=article, pool_title=pool_title)
            created.append({'number': number, 'wiki_title': article.title})
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
        # A schedule number and a deferred-queue number are independent now;
        # number-based support actions refer to the public schedule only.
        link = GameTaskGroup.objects.filter(
            game=game,
            is_deferred=False,
            number=str(number),
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
