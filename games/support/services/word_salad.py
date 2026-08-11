from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Optional

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, Project, Task, TaskGroup
from games.support.services.preview import ActorSpec, preview_task_group_url
from games.word_salad import (
    WORD_SALAD_GAME_ID,
    build_ui_context,
    format_grid_text,
    format_words_text,
    load_state,
    parse_task_data,
    serialize_task_data,
    validate_task_data,
)

WORD_SALAD_SECTION_TITLE = 'Словесный Салат'
WORD_SALAD_SECTION_ICON = '🥗'

_DEFAULT_GRID = ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M']
_DEFAULT_WORDS = ['ABCDEFGHIJKLMNOP']
_PREVIEW_SPEC = ActorSpec(kind='anon', anon_key='support-preview', play_mode='personal')


class WordSaladSupportError(Exception):
    pass


@dataclass(frozen=True)
class WordSaladRow:
    link_id: int
    task_group_id: int
    task_id: Optional[int]
    number: int
    name: str
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


def ensure_word_salad_game() -> Game:
    project = _ensure_project()
    defaults = {
        'name': WORD_SALAD_SECTION_TITLE,
        'outside_name': WORD_SALAD_SECTION_TITLE,
        'no_html_name': WORD_SALAD_SECTION_TITLE,
        'author': 'support',
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
        'tags': {},
    }
    game, created = Game.objects.get_or_create(pk=WORD_SALAD_GAME_ID, defaults=defaults)
    if created:
        return game
    project_changed = getattr(game, 'project_id', None) != 'sections'
    if project_changed:
        game.project = project
    changed = []
    if not game.name:
        game.name = WORD_SALAD_SECTION_TITLE
        changed.append('name')
    if not game.outside_name:
        game.outside_name = WORD_SALAD_SECTION_TITLE
        changed.append('outside_name')
    if not game.no_html_name:
        game.no_html_name = WORD_SALAD_SECTION_TITLE
        changed.append('no_html_name')
    if not game.author:
        game.author = 'support'
        changed.append('author')
    if not game.is_ready:
        game.is_ready = True
        changed.append('is_ready')
    if not game.is_playable:
        game.is_playable = True
        changed.append('is_playable')
    if not game.is_registrable:
        # left as-is
        pass
    if not game.is_tournament:
        pass
    if changed or project_changed:
        game.save()
    return game


def _task_for_link(link: GameTaskGroup) -> Optional[Task]:
    return Task.objects.filter(task_group_id=link.task_group_id, number='1').first()


def _default_payload() -> dict[str, Any]:
    return {'grid': _DEFAULT_GRID, 'words': _DEFAULT_WORDS}


def _sorted_links():
    game = ensure_word_salad_game()
    return GameTaskGroup.order_queryset_by_number(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        reverse=False,
    )


def _next_number() -> int:
    numbers = []
    for link in _sorted_links():
        try:
            numbers.append(int(link.number))
        except (TypeError, ValueError):
            continue
    return max(numbers) + 1 if numbers else 1


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


def list_word_salad_rows() -> list[WordSaladRow]:
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
                grid, words = parse_task_data(task.checker_data, '')
            except Exception:
                grid, words = [], []
        rows.append(WordSaladRow(
            link_id=link.pk,
            task_group_id=link.task_group_id,
            task_id=task.pk if task else None,
            number=number,
            name=link.name or f'{WORD_SALAD_SECTION_TITLE} #{number}',
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


def get_word_salad_detail(link_id: int) -> dict[str, Any]:
    link = (
        GameTaskGroup.objects.filter(game=ensure_word_salad_game(), pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WordSaladSupportError('Салат не найден')
    task = _task_for_link(link)
    if task is None:
        raise WordSaladSupportError('Задание не найдено')
    try:
        grid, words = parse_task_data(task.checker_data, '')
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
        'words_count': len(words),
        'preview_url': preview_task_group_url(WORD_SALAD_GAME_ID, link.number, _PREVIEW_SPEC),
    }


@transaction.atomic
def create_word_salad() -> dict[str, Any]:
    game = ensure_word_salad_game()
    checker = _ensure_checker_type()
    number = _next_number()
    task_group = TaskGroup.objects.create(
        label=f'{WORD_SALAD_SECTION_TITLE} #{number}',
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
        checker_data=serialize_task_data(_DEFAULT_GRID, _DEFAULT_WORDS),
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
        name=f'{WORD_SALAD_SECTION_TITLE} #{number}',
    )
    return get_word_salad_detail(link.pk)


@transaction.atomic
def update_word_salad(link_id: int, *, intro: str, grid_text: str, words_text: str, name: str | None = None) -> dict[str, Any]:
    link = (
        GameTaskGroup.objects.filter(game=ensure_word_salad_game(), pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WordSaladSupportError('Салат не найден')
    task = _task_for_link(link)
    if task is None:
        raise WordSaladSupportError('Задание не найдено')
    validate_task_data(serialize_task_data(grid_text, words_text), '')
    task.text = intro or ''
    task.checker_data = serialize_task_data(grid_text, words_text)
    task.answer = ''
    task.save(update_fields=['text', 'checker_data', 'answer'])
    if name is not None:
        new_name = (name or '').strip() or f'{WORD_SALAD_SECTION_TITLE} #{link.number}'
        link.name = new_name
        if link.task_group is not None:
            link.task_group.label = new_name
            link.task_group.save(update_fields=['label'])
        link.save(update_fields=['name'])
    return get_word_salad_detail(link.pk)


@transaction.atomic
def delete_word_salad(link_id: int) -> None:
    link = (
        GameTaskGroup.objects.filter(game=ensure_word_salad_game(), pk=link_id)
        .select_related('task_group')
        .first()
    )
    if link is None:
        raise WordSaladSupportError('Салат не найден')
    task_group = link.task_group
    link.delete()
    if task_group is not None:
        task_group.delete()


def dashboard_context(*, edit_link_id: int | None = None) -> dict[str, Any]:
    rows = list_word_salad_rows()
    detail = None
    if edit_link_id is not None:
        detail = get_word_salad_detail(edit_link_id)
    return {
        'page_title': WORD_SALAD_SECTION_TITLE,
        'rows': rows,
        'rows_json': [r.to_dict() for r in rows],
        'detail': detail,
        'dashboard_url': reverse('support:word_salad'),
        'create_url': reverse('support:word_salad_create'),
        'section_url': reverse('support:sections'),
    }
