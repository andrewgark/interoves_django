"""Support console: управление лесенками (порядок, даты, контент).

Внутренний id = GameTaskGroup.pk (и TaskGroup/Task) — стабильный.
Публичный номер = GameTaskGroup.number — порядковый; дата публикации =
ladder_publish_start + (N-1) дней (МСК). При вставке/DnD меняем только number
(и связанные title/label), pk не трогаем.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from games.ladder_daily import (
    LADDER_GAME_ID,
    LADDER_PUBLISH_START_TAG,
    MOSCOW,
    is_ladder_number_published,
    ladder_publish_at,
    ladder_publish_start,
)
from games.models import CheckerType, Game, GameTaskGroup, Task, TaskGroup
from games.raddle import (
    ensure_raddle_assist_hints,
    length_label_from_word,
    validate_raddle_checker_data,
)

AUTHOR_TAG = 'author'
_TITLE_RE = re.compile(r'^Лесенка\s*#\s*(\d+)\s*$', re.IGNORECASE)
_DEFAULT_PLACEHOLDER = {
    'words': ['СТАРТ', 'ФИНИШ'],
    'hints': ['заглушка — замените на настоящую лесенку'],
}


class LadderSupportError(Exception):
    """Ошибка операции с лесенками (валидация / конфликт)."""


@dataclass(frozen=True)
class LadderRow:
    link_id: int
    task_group_id: int
    task_id: Optional[int]
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    word_count: int
    words_preview: str
    author: str
    intro: str
    play_url: str
    mixed_script: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_ladder_game() -> Game:
    try:
        return Game.objects.get(pk=LADDER_GAME_ID)
    except Game.DoesNotExist as exc:
        raise LadderSupportError('Игра ladder не найдена') from exc


def _length_from_word(word: str):
    label = length_label_from_word(word)
    if re.match(r'^\d+$', label):
        return int(label)
    return label


def build_checker_payload(
    words: list[str],
    hints: list[str],
    *,
    mixed_script: bool = False,
) -> dict:
    word_list = [str(w or '').strip().upper() for w in words]
    word_list = [w for w in word_list if w]
    hint_list = [str(h or '').strip() for h in hints]
    if len(hint_list) > max(0, len(word_list) - 1):
        hint_list = hint_list[: max(0, len(word_list) - 1)]
    while len(hint_list) < max(0, len(word_list) - 1):
        hint_list.append('')
    payload = {
        'lengths': [_length_from_word(w) for w in word_list],
        'hints': hint_list,
        'words': word_list,
        'raddle_assist': {'enabled': True, 'fractions': [1, 0.5, 0]},
    }
    if mixed_script:
        payload['mixed_script'] = True
    return payload


def validate_ladder_content(
    words: list[str],
    hints: list[str],
    *,
    mixed_script: bool = False,
) -> list[str]:
    payload = build_checker_payload(words, hints, mixed_script=mixed_script)
    raw = json.dumps(payload, ensure_ascii=False)
    return validate_raddle_checker_data(raw, answer_text='\n'.join(payload['words']))


def _apply_title(old: str, new_num: int) -> str:
    if not old:
        return old
    if _TITLE_RE.match(old.strip()):
        return f'Лесенка #{new_num}'
    return old


def _sync_link_titles(link: GameTaskGroup, new_num: int) -> None:
    """Обновить name / label / task.text-заголовок под новый публичный номер."""
    tg = link.task_group
    link.name = f'Лесенка #{new_num}'
    if (tg.label or '').startswith('ladder:') or not (tg.label or '').strip():
        tg.label = f'ladder:{new_num}'
        tg.save(update_fields=['label'])
    task = Task.objects.filter(task_group=tg, number='1').first()
    if task and task.text:
        new_text = _apply_title(task.text, new_num)
        if new_text != task.text:
            task.text = new_text
            task.save(update_fields=['text'])


def _renumber_links(ordered_links: list[GameTaskGroup]) -> None:
    """Двухфазно выставить number = 1..N в порядке ordered_links."""
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


def _task_for_link(link: GameTaskGroup) -> Optional[Task]:
    return Task.objects.filter(task_group_id=link.task_group_id, number='1').first()


def _parse_task_payload(task: Optional[Task]) -> dict[str, Any]:
    if task is None:
        return {
            'words': [],
            'hints': [],
            'author': '',
            'intro': '',
            'mixed_script': False,
            'word_count': 0,
            'words_preview': '',
        }
    words: list[str] = []
    hints: list[str] = []
    mixed_script = False
    raw = (task.checker_data or '').strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                words = [str(w) for w in (data.get('words') or [])]
                hints = [str(h) for h in (data.get('hints') or [])]
                mixed_script = bool(data.get('mixed_script'))
        except (ValueError, TypeError):
            pass
    if not words and (task.answer or '').strip():
        words = [ln.strip() for ln in task.answer.splitlines() if ln.strip()]
    tags = task.tags or {}
    author = str(tags.get(AUTHOR_TAG) or '')
    intro = task.text or ''
    preview = ' → '.join(words[:3])
    if len(words) > 3:
        preview += ' → …'
    return {
        'words': words,
        'hints': hints,
        'author': author,
        'intro': intro,
        'mixed_script': mixed_script,
        'word_count': len(words),
        'words_preview': preview,
    }


def list_ladder_rows(*, now: datetime | None = None) -> list[LadderRow]:
    game = get_ladder_game()
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    links = GameTaskGroup.sorted_links(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )
    rows: list[LadderRow] = []
    for link in links:
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            continue
        task = _task_for_link(link)
        payload = _parse_task_payload(task)
        pub = ladder_publish_at(game, number)
        pub_date = pub.date().isoformat() if pub else None
        is_pub = is_ladder_number_published(game, number, now)
        is_today = bool(pub and pub.date() == today)
        rows.append(LadderRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            task_id=task.pk if task else None,
            number=number,
            name=link.name or f'Лесенка #{number}',
            publish_date=pub_date,
            is_published=is_pub,
            is_today=is_today,
            word_count=payload['word_count'],
            words_preview=payload['words_preview'],
            author=payload['author'],
            intro=payload['intro'],
            play_url=f'/games/{LADDER_GAME_ID}/{number}/',
            mixed_script=bool(payload.get('mixed_script')),
        ))
    return rows


def get_ladder_detail(link_id: int) -> dict[str, Any]:
    game = get_ladder_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise LadderSupportError('Лесенка не найдена')
    task = _task_for_link(link)
    payload = _parse_task_payload(task)
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    pub = ladder_publish_at(game, number)
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'task_id': task.pk if task else None,
        'number': number,
        'name': link.name,
        'publish_date': pub.date().isoformat() if pub else None,
        'intro': payload['intro'],
        'author': payload['author'],
        'words': payload['words'],
        'hints': payload['hints'],
        'mixed_script': payload['mixed_script'],
        'play_url': f'/games/{LADDER_GAME_ID}/{number}/',
    }


def get_publish_start_iso() -> Optional[str]:
    start = ladder_publish_start(get_ladder_game())
    if start is None:
        return None
    return start.date().isoformat()


@transaction.atomic
def set_publish_start(date_iso: str) -> str:
    """Установить дату №1 (YYYY-MM-DD, полночь МСК)."""
    try:
        d = date.fromisoformat(str(date_iso).strip()[:10])
    except ValueError as exc:
        raise LadderSupportError('Некорректная дата publish_start') from exc
    game = get_ladder_game()
    tags = dict(game.tags or {})
    tags[LADDER_PUBLISH_START_TAG] = f'{d.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.save(update_fields=['tags'])
    return d.isoformat()


def last_published_number(*, now: datetime | None = None) -> int:
    """Максимальный публичный № среди уже вышедших; 0 если таких нет."""
    published = [r.number for r in list_ladder_rows(now=now) if r.is_published]
    return max(published) if published else 0


def _assert_future_only_order(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> None:
    """Вышедшие лесенки должны остаться префиксом 1..K в том же порядке."""
    current = list_ladder_rows(now=now)
    locked = [r for r in current if r.is_published]
    if not locked:
        return
    locked_ids = [r.link_id for r in locked]
    if ordered_link_ids[: len(locked_ids)] != locked_ids:
        last = locked[-1].number
        raise LadderSupportError(
            'Нельзя менять порядок уже вышедших лесенок (№1–{}). '
            'Переставляйте только будущие.'.format(last)
        )


@transaction.atomic
def reorder_ladders(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[LadderRow]:
    """Выставить публичные номера 1..N по порядку link_id.

    Уже вышедшие зафиксированы: в ``ordered_link_ids`` они должны идти
    префиксом в текущем порядке. Переставлять можно только будущие.
    """
    game = get_ladder_game()
    if not ordered_link_ids:
        raise LadderSupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise LadderSupportError('Дубликаты id в порядке')

    existing = list(
        GameTaskGroup.objects.filter(game=game).select_related('task_group')
    )
    by_id = {link.pk: link for link in existing}
    if set(ordered_link_ids) != set(by_id):
        raise LadderSupportError(
            'Список id не совпадает с текущими лесенками '
            '(обновите страницу и повторите)'
        )
    _assert_future_only_order(ordered_link_ids, now=now)
    ordered = [by_id[pk] for pk in ordered_link_ids]
    _renumber_links(ordered)
    return list_ladder_rows(now=now)


def _create_task_group_and_task(
    *,
    number: int,
    words: list[str],
    hints: list[str],
    intro: str,
    author: str,
    mixed_script: bool = False,
) -> GameTaskGroup:
    errors = validate_ladder_content(words, hints, mixed_script=mixed_script)
    if errors:
        raise LadderSupportError('; '.join(errors))
    payload = build_checker_payload(words, hints, mixed_script=mixed_script)
    checker = CheckerType.objects.get(id='raddle')
    game = get_ladder_game()
    task_group = TaskGroup.objects.create(
        label=f'ladder:{number}',
        checker=checker,
        points=1,
        max_attempts=3,
    )
    tags = {}
    if author.strip():
        tags[AUTHOR_TAG] = author.strip()
    task = Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='raddle',
        checker=checker,
        checker_data=json.dumps(payload, ensure_ascii=False),
        answer='\n'.join(payload['words']),
        text=intro or '',
        tags=tags,
        points=1,
        max_attempts=None,
        is_removed=False,
    )
    ensure_raddle_assist_hints(task)
    link = GameTaskGroup.objects.create(
        game=game,
        task_group=task_group,
        number=str(number),
        name=f'Лесенка #{number}',
    )
    return link


@transaction.atomic
def create_ladder(
    *,
    at_number: int,
    words: list[str] | None = None,
    hints: list[str] | None = None,
    intro: str = '',
    author: str = '',
    mixed_script: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Вставить лесенку с публичным номером at_number; сдвинуть остальные вверх.

    Вставка среди уже вышедших запрещена (только после последнего вышедшего №).
    """
    if at_number < 1:
        raise LadderSupportError('Номер должен быть >= 1')
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise LadderSupportError(
            'Нельзя вставлять среди уже вышедших лесенок '
            '(доступно с №{})'.format(locked_until + 1)
        )
    game = get_ladder_game()
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

    # Сдвиг существующих с number >= at_number (с высоких, через temp).
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

    use_words = words if words is not None else list(_DEFAULT_PLACEHOLDER['words'])
    use_hints = hints if hints is not None else list(_DEFAULT_PLACEHOLDER['hints'])
    link = _create_task_group_and_task(
        number=at_number,
        words=use_words,
        hints=use_hints,
        intro=intro,
        author=author,
        mixed_script=mixed_script,
    )
    return get_ladder_detail(link.pk)


@transaction.atomic
def update_ladder(
    link_id: int,
    *,
    words: list[str],
    hints: list[str],
    intro: str = '',
    author: str = '',
    mixed_script: bool = False,
) -> dict[str, Any]:
    game = get_ladder_game()
    link = (
        GameTaskGroup.objects.filter(game=game, pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise LadderSupportError('Лесенка не найдена')
    errors = validate_ladder_content(words, hints, mixed_script=mixed_script)
    if errors:
        raise LadderSupportError('; '.join(errors))
    payload = build_checker_payload(words, hints, mixed_script=mixed_script)
    checker = CheckerType.objects.get(id='raddle')
    tags = {}
    if author.strip():
        tags[AUTHOR_TAG] = author.strip()
    task, _created = Task.objects.update_or_create(
        task_group=link.task_group,
        number='1',
        defaults={
            'task_type': 'raddle',
            'checker': checker,
            'checker_data': json.dumps(payload, ensure_ascii=False),
            'answer': '\n'.join(payload['words']),
            'text': intro or '',
            'tags': tags,
            'points': 1,
            'max_attempts': None,
            'is_removed': False,
        },
    )
    ensure_raddle_assist_hints(task)
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    if number:
        _sync_link_titles(link, number)
        link.save(update_fields=['name'])
    return get_ladder_detail(link.pk)


def dashboard_context(*, now: datetime | None = None) -> dict[str, Any]:
    rows = list_ladder_rows(now=now)
    published = sum(1 for r in rows if r.is_published)
    future = len(rows) - published
    locked_until = last_published_number(now=now)
    return {
        'ladders': rows,
        'ladders_json': [r.to_dict() for r in rows],
        'publish_start': get_publish_start_iso(),
        'ladder_count': len(rows),
        'published_count': published,
        'future_count': future,
        'today_number': next((r.number for r in rows if r.is_today), None),
        'locked_until': locked_until,
    }
