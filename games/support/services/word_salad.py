from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, Project, Task, TaskGroup
from games.support.services.preview import ActorSpec, preview_task_group_url
from games.support.services.schedule_links import (
    assert_future_only_order,
    build_schedule_page_context,
    delete_future_slot,
    renumber_links,
)
from games.word_salad import (
    WORD_SALAD_GAME_ID,
    format_grid_text,
    format_words_text,
    parse_task_payload,
    serialize_task_data,
    validate_puzzle,
)
from games.word_salad_daily import (
    WORD_SALAD_DEFAULT_PUBLISH_START,
    WORD_SALAD_PUBLISH_START_TAG,
    is_word_salad_number_published,
    word_salad_publish_at,
    word_salad_publish_start,
)
from games.daily_section import MOSCOW

WORD_SALAD_SECTION_TITLE = 'Салатик'
WORD_SALAD_SECTION_ICON = '🥗'
_OLD_SECTION_TITLES = frozenset({'Словесный Салат', 'Словесный салат', 'Салат'})

_DEFAULT_GRID = ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M']
_DEFAULT_WORDS = ['ABCDEFGHIJKLMNOP']
_PREVIEW_SPEC = ActorSpec(kind='anon', anon_key='support-preview', play_mode='personal')
_TITLE_RE = re.compile(r'^(?:Словесный\s+)?Салат(?:ик)?\s*#\s*\d+$', re.IGNORECASE)


def _salad_title(number: int) -> str:
    return f'{WORD_SALAD_SECTION_TITLE} #{number}'


class WordSaladSupportError(Exception):
    pass


@dataclass(frozen=True)
class WordSaladRow:
    link_id: int
    task_group_id: int
    task_id: Optional[int]
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    grid_preview: str
    words_preview: str
    words_count: int
    preview_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_project():
    project, _ = Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    return project


def _ensure_checker_type() -> CheckerType:
    checker, _ = CheckerType.objects.get_or_create(pk='word_salad')
    return checker


def _is_legacy_game_title(value: str | None) -> bool:
    text = (value or '').strip()
    return not text or text in _OLD_SECTION_TITLES


def ensure_word_salad_game() -> Game:
    project = _ensure_project()
    defaults = {
        'name': WORD_SALAD_SECTION_TITLE,
        'outside_name': WORD_SALAD_SECTION_TITLE,
        'no_html_name': WORD_SALAD_SECTION_TITLE,
        'author': 'Interoves',
        'author_extra': '',
        'project': project,
        'start_time': timezone.now(),
        'end_time': timezone.now() + timedelta(days=3650),
        'visible_start_time': None,
        'visible_end_time': None,
        'is_ready': True,
        'is_testing': False,
        'is_registrable': False,
        'requires_ticket': False,
        'is_playable': True,
        'is_tournament': False,
        'game_url': '',
        'answers_url': '',
        'standings_url': '',
        'theme': 'Сетка 4×4: найдите все слова по соседним буквам.',
        'tags': {WORD_SALAD_PUBLISH_START_TAG: WORD_SALAD_DEFAULT_PUBLISH_START},
    }
    game, created = Game.objects.get_or_create(pk=WORD_SALAD_GAME_ID, defaults=defaults)
    if created:
        return game
    project_changed = getattr(game, 'project_id', None) != 'sections'
    if project_changed:
        game.project = project
    changed = []
    if _is_legacy_game_title(game.name):
        game.name = WORD_SALAD_SECTION_TITLE
        changed.append('name')
    if _is_legacy_game_title(game.outside_name):
        game.outside_name = WORD_SALAD_SECTION_TITLE
        changed.append('outside_name')
    if _is_legacy_game_title(game.no_html_name):
        game.no_html_name = WORD_SALAD_SECTION_TITLE
        changed.append('no_html_name')
    if not game.author or game.author == 'support':
        game.author = 'Interoves'
        changed.append('author')
    if not game.is_ready:
        game.is_ready = True
        changed.append('is_ready')
    if not game.is_playable:
        game.is_playable = True
        changed.append('is_playable')
    tags = dict(game.tags or {})
    if not tags.get(WORD_SALAD_PUBLISH_START_TAG):
        tags[WORD_SALAD_PUBLISH_START_TAG] = WORD_SALAD_DEFAULT_PUBLISH_START
        game.tags = tags
        changed.append('tags')
    if changed or project_changed:
        game.save()
    return game


def get_word_salad_game() -> Game:
    try:
        return Game.objects.get(pk=WORD_SALAD_GAME_ID)
    except Game.DoesNotExist as exc:
        raise WordSaladSupportError('Игра word_salad не найдена') from exc


def _task_for_link(link: GameTaskGroup) -> Optional[Task]:
    return Task.objects.filter(task_group_id=link.task_group_id, number='1').first()


def _sorted_links():
    game = Game.objects.filter(pk=WORD_SALAD_GAME_ID).first()
    if game is None:
        return GameTaskGroup.objects.none()
    return GameTaskGroup.order_queryset_by_number(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )


def _temporary_number(links: list[GameTaskGroup]) -> int:
    numbers = []
    for link in links:
        try:
            numbers.append(int(link.number))
        except (TypeError, ValueError):
            continue
    return max(numbers, default=0) + 10_000


def _sync_link_titles(link: GameTaskGroup, new_num: int) -> None:
    """Keep name / label in lockstep with the public number, like ladders."""
    title = _salad_title(new_num)
    link.name = title
    task_group = link.task_group
    if task_group is not None:
        desired_label = f'salad:{new_num}'
        if (task_group.label or '').strip() != desired_label:
            task_group.label = desired_label
            task_group.save(update_fields=['label'])
    task = _task_for_link(link)
    if task and task.text and _TITLE_RE.match(task.text.strip()):
        task.text = ''
        task.save(update_fields=['text'])


def _renumber_links(ordered_links: list[GameTaskGroup]) -> None:
    renumber_links(ordered_links, sync_link=_sync_link_titles)


def _validated_checker_data(grid_value, words_value, rare_words_value=None) -> str:
    try:
        validate_puzzle(grid_value, words_value, rare_words_value)
        return serialize_task_data(grid_value, words_value, rare_words_value)
    except ValueError as exc:
        raise WordSaladSupportError(str(exc)) from exc


def _preview_text(values, *, max_items=3):
    values = [v for v in values if v]
    if not values:
        return '—'
    preview = ' → '.join(values[:max_items])
    if len(values) > max_items:
        preview += ' → …'
    return preview


def _grid_preview(grid):
    if not grid:
        return '—'
    return ' '.join(grid[:4]) + ' / ' + ' '.join(grid[4:8])


def list_word_salad_rows(*, now: datetime | None = None) -> list[WordSaladRow]:
    now = now or timezone.now()
    today = now.astimezone(MOSCOW).date()
    game = Game.objects.filter(pk=WORD_SALAD_GAME_ID).first()
    rows = []
    for link in _sorted_links():
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            continue
        task = _task_for_link(link)
        grid = []
        words = []
        if task is not None:
            try:
                grid, words, _rare_words = parse_task_payload(task.checker_data, '')
            except Exception:
                grid, words = [], []
        published_at = word_salad_publish_at(game, number) if game is not None else None
        rows.append(WordSaladRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            task_id=task.pk if task else None,
            number=number,
            name=link.name or _salad_title(number),
            publish_date=published_at.date().isoformat() if published_at else None,
            is_published=(
                is_word_salad_number_published(game, number, now)
                if game is not None else False
            ),
            is_today=bool(published_at and published_at.date() == today),
            grid_preview=_grid_preview(grid),
            words_preview=_preview_text(words),
            words_count=len(words),
            preview_url=preview_task_group_url(
                WORD_SALAD_GAME_ID,
                link.number,
                _PREVIEW_SPEC,
            ),
        ))
    return rows


def get_publish_start_iso() -> Optional[str]:
    game = Game.objects.filter(pk=WORD_SALAD_GAME_ID).first()
    if game is None:
        return None
    start = word_salad_publish_start(game)
    return start.date().isoformat() if start else None


@transaction.atomic
def set_publish_start(date_iso: str) -> str:
    try:
        publish_date = date.fromisoformat(str(date_iso).strip()[:10])
    except ValueError as exc:
        raise WordSaladSupportError('Некорректная дата publish_start') from exc
    game = ensure_word_salad_game()
    tags = dict(game.tags or {})
    tags[WORD_SALAD_PUBLISH_START_TAG] = f'{publish_date.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.save(update_fields=['tags'])
    return publish_date.isoformat()


def last_published_number(*, now: datetime | None = None) -> int:
    published = [row.number for row in list_word_salad_rows(now=now) if row.is_published]
    return max(published) if published else 0


def get_word_salad_detail(link_id: int) -> dict[str, Any]:
    link = (
        GameTaskGroup.objects.filter(game=get_word_salad_game(), pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WordSaladSupportError('Салатик не найден')
    task = _task_for_link(link)
    if task is None:
        raise WordSaladSupportError('Задание не найдено')
    try:
        grid, words, rare_words = parse_task_payload(task.checker_data, '')
    except Exception as exc:
        raise WordSaladSupportError(str(exc)) from exc
    return {
        'link_id': link.pk,
        'task_group_id': link.task_group_id,
        'task_id': task.pk,
        'number': int(link.number) if str(link.number).isdigit() else link.number,
        'name': link.name,
        'intro': task.text or '',
        'grid_text': format_grid_text(grid),
        'words_text': format_words_text(words),
        'rare_words_text': format_words_text(rare_words),
        'words_count': len(words),
        'preview_url': preview_task_group_url(WORD_SALAD_GAME_ID, link.number, _PREVIEW_SPEC),
    }


@transaction.atomic
def create_word_salad(
    *,
    at_number: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    game = ensure_word_salad_game()
    checker = _ensure_checker_type()
    links = list(_sorted_links())
    if at_number is None:
        at_number = len(links) + 1
    try:
        at_number = int(at_number)
    except (TypeError, ValueError) as exc:
        raise WordSaladSupportError('Некорректная позиция вставки') from exc
    if not 1 <= at_number <= len(links) + 1:
        raise WordSaladSupportError('Позиция вставки должна быть от 1 до {}'.format(len(links) + 1))
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise WordSaladSupportError(
            'Нельзя вставлять среди уже вышедших салатиков '
            '(доступно с №{})'.format(locked_until + 1)
        )
    number = _temporary_number(links)
    task_group = TaskGroup.objects.create(
        label=f'salad:{at_number}',
        checker=checker,
        points=1,
        max_attempts=None,
        is_18_plus=False,
    )
    Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='word_salad',
        checker=checker,
        checker_data=_validated_checker_data(_DEFAULT_GRID, _DEFAULT_WORDS),
        answer='',
        text='',
        points=1,
        max_attempts=None,
        is_removed=False,
    )
    link = GameTaskGroup.objects.create(
        game=game,
        task_group=task_group,
        number=str(number),
        name=_salad_title(at_number),
    )
    links.insert(at_number - 1, link)
    _renumber_links(links)
    return get_word_salad_detail(link.pk)


@transaction.atomic
def attach_existing_task_group(
    task_group: TaskGroup,
    *,
    at_number: int,
    now: datetime | None = None,
) -> GameTaskGroup:
    """Вставить уже существующий TaskGroup в расписание салатиков."""
    game = get_word_salad_game()
    if GameTaskGroup.objects.filter(game=game, task_group=task_group).exists():
        raise WordSaladSupportError('Этот набор заданий уже в расписании салатиков')
    links = list(_sorted_links())
    try:
        at_number = int(at_number)
    except (TypeError, ValueError) as exc:
        raise WordSaladSupportError('Некорректная позиция вставки') from exc
    if at_number < 1:
        at_number = 1
    if at_number > len(links) + 1:
        at_number = len(links) + 1
    locked_until = last_published_number(now=now)
    if at_number <= locked_until:
        raise WordSaladSupportError(
            'Нельзя вставлять среди уже вышедших салатиков '
            '(доступно с №{})'.format(locked_until + 1)
        )
    if (task_group.label or '').startswith('salad:') or not (task_group.label or '').strip():
        task_group.label = f'salad:{at_number}'
        task_group.save(update_fields=['label'])
    elif (task_group.label or '').startswith('salad_offer:'):
        task_group.label = f'salad:{at_number}'
        task_group.save(update_fields=['label'])
    number = _temporary_number(links)
    link = GameTaskGroup.objects.create(
        game=game,
        task_group=task_group,
        number=str(number),
        name=_salad_title(at_number),
    )
    links.insert(at_number - 1, link)
    _renumber_links(links)
    return link


@transaction.atomic
def update_word_salad(
    link_id: int,
    *,
    intro: str,
    grid_text: str,
    words_text: str,
    rare_words_text: str = '',
) -> dict[str, Any]:
    link = (
        GameTaskGroup.objects.filter(game=get_word_salad_game(), pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WordSaladSupportError('Салатик не найден')
    task = _task_for_link(link)
    if task is None:
        raise WordSaladSupportError('Задание не найдено')
    checker_data = _validated_checker_data(grid_text, words_text, rare_words_text)
    task.text = intro or ''
    task.checker_data = checker_data
    task.answer = ''
    task.save(update_fields=['text', 'checker_data', 'answer'])
    try:
        number = int(link.number)
    except (TypeError, ValueError):
        number = 0
    if number:
        _sync_link_titles(link, number)
        link.save(update_fields=['name'])
    return get_word_salad_detail(link.pk)


@transaction.atomic
def reorder_word_salads(
    ordered_link_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[WordSaladRow]:
    game = get_word_salad_game()
    if not ordered_link_ids:
        raise WordSaladSupportError('Пустой порядок')
    if len(set(ordered_link_ids)) != len(ordered_link_ids):
        raise WordSaladSupportError('Дубликаты id в порядке')
    existing = list(
        GameTaskGroup.objects.filter(game=game).select_related('task_group')
    )
    by_id = {link.pk: link for link in existing}
    if set(ordered_link_ids) != set(by_id):
        raise WordSaladSupportError(
            'Список id не совпадает с текущими салатиками '
            '(обновите страницу и повторите)'
        )
    assert_future_only_order(
        ordered_link_ids,
        list_word_salad_rows(now=now),
        error_cls=WordSaladSupportError,
        published_msg='Нельзя менять порядок уже вышедших салатиков (№1–{number}). '
        'Переставляйте только будущие.',
    )
    _renumber_links([by_id[pk] for pk in ordered_link_ids])
    return list_word_salad_rows(now=now)


@transaction.atomic
def delete_word_salad(
    link_id: int,
    *,
    now: datetime | None = None,
) -> list[WordSaladRow]:
    game = get_word_salad_game()
    return delete_future_slot(
        game=game,
        link_id=link_id,
        is_number_published=is_word_salad_number_published,
        renumber_links=_renumber_links,
        list_rows=list_word_salad_rows,
        error_cls=WordSaladSupportError,
        not_found_msg='Салатик не найден',
        published_msg='Нельзя удалять уже вышедший салатик №{number}',
        now=now,
    )


def dashboard_context(
    *,
    edit_link_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = list_word_salad_rows(now=now)
    detail = None
    if edit_link_id is not None:
        detail = get_word_salad_detail(edit_link_id)
    return {
        'page_title': WORD_SALAD_SECTION_TITLE,
        **build_schedule_page_context(
            rows,
            title=WORD_SALAD_SECTION_TITLE,
            prefix='word-salad',
            list_label='Список выпусков Салатика',
            publish_start=get_publish_start_iso(),
        ),
        'rows': rows,
        'rows_json': [r.to_dict() for r in rows],
        'detail': detail,
        'dashboard_url': reverse('support:word_salad'),
        'create_url': reverse('support:word_salad_create'),
        'section_url': reverse('support:sections'),
    }
