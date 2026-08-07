"""Support console: расписание Алфавитки (порядок, даты, слова, генерация)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.alphabetty.core import normalize_word, pick_answer_words
from games.alphabetty.play import ALPHABETTY_BASE_POINTS
from games.alphabetty_daily import (
    ALPHABETTY_BUFFER_DAYS,
    ALPHABETTY_GAME_ID,
    ALPHABETTY_PUBLISH_START_TAG,
    MOSCOW,
    alphabetty_publish_at,
    alphabetty_publish_start,
    current_alphabetty_number,
    is_alphabetty_number_published,
)
from games.models import CheckerType, Game, GameTaskGroup, Task, TaskGroup
from games.support.services.banned import (
    add_banned_word,
    banned_word_set,
    list_banned_words,
    remove_banned_word,
)
from games.support.services.schedule_links import delete_future_slot

logger = logging.getLogger(__name__)


class AlphabettySupportError(Exception):
    """Ошибка операции с алфавитками."""


@dataclass(frozen=True)
class AlphabettyRow:
    link_id: int
    task_group_id: int
    task_id: Optional[int]
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    word: str
    play_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_alphabetty_game() -> Game:
    try:
        return Game.objects.get(pk=ALPHABETTY_GAME_ID)
    except Game.DoesNotExist as exc:
        raise AlphabettySupportError('Игра alphabetty не найдена') from exc


def _task_for_link(link: GameTaskGroup) -> Optional[Task]:
    return Task.objects.filter(task_group_id=link.task_group_id, number='1').first()


def _word_from_task(task: Optional[Task]) -> str:
    if task is None:
        return ''
    return normalize_word((task.answer or '').strip().splitlines()[0] if task.answer else '')


def _sync_link_titles(link: GameTaskGroup, new_num: int) -> None:
    tg = link.task_group
    link.name = f'Алфавитка #{new_num}'
    if (tg.label or '').startswith('alphabetty:') or not (tg.label or '').strip():
        tg.label = f'alphabetty:{new_num}'
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


def list_alphabetty_rows(*, now: datetime | None = None) -> list[AlphabettyRow]:
    game = get_alphabetty_game()
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    rows: list[AlphabettyRow] = []
    for link in links:
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            continue
        task = _task_for_link(link)
        word = _word_from_task(task)
        pub = alphabetty_publish_at(game, number)
        pub_date = pub.date().isoformat() if pub else None
        is_pub = is_alphabetty_number_published(game, number, now)
        is_today = bool(pub and pub.date() == today)
        rows.append(AlphabettyRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            task_id=task.pk if task else None,
            number=number,
            name=link.name or f'Алфавитка #{number}',
            publish_date=pub_date,
            is_published=is_pub,
            is_today=is_today,
            word=word,
            play_url=f'/{ALPHABETTY_GAME_ID}/{number}/',
        ))
    return rows


def get_alphabetty_detail(link_id: int) -> dict[str, Any]:
    game = get_alphabetty_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise AlphabettySupportError('Алфавитка не найдена')
    task = _task_for_link(link)
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    pub = alphabetty_publish_at(game, number)
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'task_id': task.pk if task else None,
        'number': number,
        'name': link.name,
        'publish_date': pub.date().isoformat() if pub else None,
        'word': _word_from_task(task),
        'play_url': f'/{ALPHABETTY_GAME_ID}/{number}/',
    }


def get_publish_start_iso() -> Optional[str]:
    start = alphabetty_publish_start(get_alphabetty_game())
    if start is None:
        return None
    return start.date().isoformat()


@transaction.atomic
def set_publish_start(date_iso: str) -> str:
    try:
        d = date.fromisoformat(str(date_iso).strip()[:10])
    except ValueError as exc:
        raise AlphabettySupportError('Некорректная дата publish_start') from exc
    game = get_alphabetty_game()
    tags = dict(game.tags or {})
    tags[ALPHABETTY_PUBLISH_START_TAG] = f'{d.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.save(update_fields=['tags'])
    return d.isoformat()


def last_published_number(*, now: datetime | None = None) -> int:
    published = [r.number for r in list_alphabetty_rows(now=now) if r.is_published]
    return max(published) if published else 0


def scheduled_words(*, now: datetime | None = None) -> set[str]:
    game = get_alphabetty_game()
    return {r.word for r in list_alphabetty_rows(now=now) if r.word} | banned_word_set(game)


def _assert_future_only_order(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> None:
    current = list_alphabetty_rows(now=now)
    locked = [r for r in current if r.is_published]
    if not locked:
        return
    locked_ids = [r.link_id for r in locked]
    if ordered_link_ids[: len(locked_ids)] != locked_ids:
        last = locked[-1].number
        raise AlphabettySupportError(
            'Нельзя менять порядок уже вышедших алфавиток (№1–{}). '
            'Переставляйте только будущие.'.format(last)
        )


@transaction.atomic
def reorder_alphabetty(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[AlphabettyRow]:
    game = get_alphabetty_game()
    if not ordered_link_ids:
        raise AlphabettySupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise AlphabettySupportError('Дубликаты id в порядке')

    existing = list(
        GameTaskGroup.objects.filter(game=game).select_related('task_group')
    )
    by_id = {link.pk: link for link in existing}
    if set(ordered_link_ids) != set(by_id):
        raise AlphabettySupportError(
            'Список id не совпадает с текущими алфавитками '
            '(обновите страницу и повторите)'
        )
    _assert_future_only_order(ordered_link_ids, now=now)
    ordered = [by_id[pk] for pk in ordered_link_ids]
    _renumber_links(ordered)
    return list_alphabetty_rows(now=now)


def _create_slot(*, number: int, word: str) -> GameTaskGroup:
    word_n = normalize_word(word)
    if not word_n:
        raise AlphabettySupportError('Пустое слово')
    checker = CheckerType.objects.get(id='alphabetty')
    game = get_alphabetty_game()
    task_group = TaskGroup.objects.create(
        label=f'alphabetty:{number}',
        checker=checker,
        points=ALPHABETTY_BASE_POINTS,
        max_attempts=None,
    )
    Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='alphabetty',
        checker=checker,
        checker_data=word_n,
        answer=word_n,
        text='',
        tags={},
        points=ALPHABETTY_BASE_POINTS,
        max_attempts=None,
        is_removed=False,
    )
    return GameTaskGroup.objects.create(
        game=game,
        task_group=task_group,
        number=str(number),
        name=f'Алфавитка #{number}',
    )


@transaction.atomic
def create_alphabetty(
    *,
    at_number: int,
    word: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if at_number < 1:
        raise AlphabettySupportError('Номер должен быть >= 1')
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise AlphabettySupportError(
            'Нельзя вставлять среди уже вышедших '
            '(доступно с №{})'.format(locked_until + 1)
        )
    game = get_alphabetty_game()
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    max_num = 0
    for link in links:
        try:
            max_num = max(max_num, int(link.number))
        except (TypeError, ValueError):
            pass
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

    if word is None:
        picked = pick_answer_words(1, exclude=scheduled_words(now=now))
        if not picked:
            raise AlphabettySupportError('Не осталось слов в пуле загадок')
        word = picked[0]
    else:
        word_n = normalize_word(word)
        if word_n in scheduled_words(now=now):
            raise AlphabettySupportError(f'Слово уже занято другим днём: {word_n}')
    link = _create_slot(number=at_number, word=word)
    return get_alphabetty_detail(link.pk)


@transaction.atomic
def update_alphabetty(link_id: int, *, word: str, now: datetime | None = None) -> dict[str, Any]:
    game = get_alphabetty_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise AlphabettySupportError('Алфавитка не найдена')
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    if number and is_alphabetty_number_published(game, number, now):
        raise AlphabettySupportError(
            f'Нельзя менять слово уже вышедшей алфавитки №{number}'
        )
    word_n = normalize_word(word)
    if not word_n:
        raise AlphabettySupportError('Пустое слово')
    taken = scheduled_words(now=now) - {_word_from_task(_task_for_link(link))}
    if word_n in taken:
        raise AlphabettySupportError(f'Слово уже занято другим днём: {word_n}')
    checker = CheckerType.objects.get(id='alphabetty')
    Task.objects.update_or_create(
        task_group=link.task_group,
        number='1',
        defaults={
            'task_type': 'alphabetty',
            'checker': checker,
            'checker_data': word_n,
            'answer': word_n,
            'text': '',
            'tags': {},
            'points': ALPHABETTY_BASE_POINTS,
            'max_attempts': None,
            'is_removed': False,
        },
    )
    if number:
        _sync_link_titles(link, number)
        link.save(update_fields=['name'])
    return get_alphabetty_detail(link.pk)


@transaction.atomic
def generate_more(n: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Дописать в конец N новых дней с уникальными словами."""
    if n < 1:
        raise AlphabettySupportError('N должно быть >= 1')
    if n > 365:
        raise AlphabettySupportError('N слишком большое (макс. 365)')
    game = get_alphabetty_game()
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game),
        reverse=False,
    )
    max_num = 0
    for link in links:
        try:
            max_num = max(max_num, int(link.number))
        except (TypeError, ValueError):
            pass
    exclude = scheduled_words(now=now)
    words = pick_answer_words(n, exclude=exclude)
    if len(words) < n:
        raise AlphabettySupportError(
            'В пуле осталось только {} слов (нужно {})'.format(len(words), n)
        )
    created = []
    for i, word in enumerate(words):
        link = _create_slot(number=max_num + 1 + i, word=word)
        created.append(get_alphabetty_detail(link.pk))
    return {
        'created_count': len(created),
        'created': created,
        'rows': [r.to_dict() for r in list_alphabetty_rows(now=now)],
    }


@transaction.atomic
def ensure_future_buffer(
    target: int = ALPHABETTY_BUFFER_DAYS,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Если будущих слотов меньше target — досэмплить до target."""
    now = now or timezone.now()
    rows = list_alphabetty_rows(now=now)
    future = [r for r in rows if not r.is_published]
    if len(future) >= target:
        return {'added': 0, 'future': len(future), 'target': target}
    initial_future = len(future)
    created_total = 0
    try:
        while len(future) < target:
            need = target - len(future)
            result = generate_more(need, now=now)
            created_total += result['created_count']
            rows = list_alphabetty_rows(now=now)
            future = [r for r in rows if not r.is_published]
            if result['created_count'] == 0:
                break
    except AlphabettySupportError as exc:
        logger.warning('alphabetty ensure_future_buffer: %s', exc)
        return {
            'added': max(0, len(future) - initial_future),
            'created': created_total,
            'future': len(future),
            'target': target,
            'error': str(exc),
        }
    except Exception as exc:
        # Гонка multi-instance на unique (game, number).
        logger.warning('alphabetty ensure_future_buffer race: %s', exc)
        return {
            'added': max(0, len(future) - initial_future),
            'created': created_total,
            'future': len(future),
            'target': target,
            'error': str(exc),
        }
    added_future = max(0, len(future) - initial_future)
    logger.info(
        'alphabetty ensure_future_buffer: added %s future slots (%s created, target %s)',
        added_future,
        created_total,
        target,
    )
    return {'added': added_future, 'created': created_total, 'future': len(future), 'target': target}


@transaction.atomic
def delete_alphabetty(link_id: int, *, now: datetime | None = None) -> list[AlphabettyRow]:
    """Удалить будущую алфавитку (слово может снова попасть в генерацию)."""
    game = get_alphabetty_game()
    return delete_future_slot(
        game=game,
        link_id=link_id,
        is_number_published=is_alphabetty_number_published,
        renumber_links=_renumber_links,
        list_rows=list_alphabetty_rows,
        error_cls=AlphabettySupportError,
        not_found_msg='Алфавитка не найдена',
        published_msg='Нельзя удалять уже вышедшую алфавитку №{number}',
        now=now,
    )


@transaction.atomic
def forbid_alphabetty(link_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Удалить будущую алфавитку и запретить её слово для генерации."""
    game = get_alphabetty_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise AlphabettySupportError('Алфавитка не найдена')
    word = _word_from_task(_task_for_link(link))
    rows = delete_alphabetty(link_id, now=now)
    banned = add_banned_word(game, word) if word else list_banned_words(game)
    return {
        'rows': [r.to_dict() for r in rows],
        'banned': banned,
    }


def unban_alphabetty_word(word: str) -> list[dict[str, Any]]:
    game = get_alphabetty_game()
    return remove_banned_word(game, word)


def alphabetty_dashboard_context(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    rows = list_alphabetty_rows(now=now)
    game = get_alphabetty_game()
    today_number = current_alphabetty_number(game, now)
    published_count = sum(1 for r in rows if r.is_published)
    future_count = len(rows) - published_count
    return {
        'rows': rows,
        'alphabetty_json': [r.to_dict() for r in rows],
        'banned_json': list_banned_words(game),
        'publish_start': get_publish_start_iso(),
        'alphabetty_count': len(rows),
        'published_count': published_count,
        'future_count': future_count,
        'today_number': today_number,
        'buffer_days': ALPHABETTY_BUFFER_DAYS,
    }
