"""Пул кандидатов и снимки TaskGroup для «Задания недели»."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from typing import Any, Optional

from django.db import transaction

from games.models import Game, GameTaskGroup, Task, TaskGroup

logger = logging.getLogger(__name__)

SOURCE_PROJECT_ID = 'main'
DES_GAME_RE = re.compile(r'^des\d+$')
EXCLUDED_TITLE_SUBSTRINGS = ('Замены', 'Стен', 'Палиндром')
SEQUENCES_SUBSTRING = 'Последовательн'
WEEK_TASK_SOURCE_TAG = 'week_task_source'


@dataclass(frozen=True)
class WeekUnit:
    """Кандидат на одно задание недели (только для генерации)."""

    source_game_id: str
    source_task_group_id: int
    source_gtg_name: str
    source_tg_label: str
    source_tg_view: str
    task_numbers: Optional[tuple[str, ...]]  # None = весь круг
    major: Optional[str]  # старший номер пункта при split
    desyatka_label: str

    @property
    def exclude_key(self) -> tuple[int, frozenset[str] | None]:
        nums = None if self.task_numbers is None else frozenset(self.task_numbers)
        return (self.source_task_group_id, nums)

    def display_name(self) -> str:
        base = (self.source_gtg_name or self.source_tg_label or 'Задание').strip()
        if self.major is not None:
            return f'{base} · {self.major}'[:100]
        return base[:100]


def _title_blob(gtg: GameTaskGroup) -> str:
    return f'{gtg.name or ""} {(gtg.task_group.label if gtg.task_group_id else "") or ""}'


def _is_excluded_genre(gtg: GameTaskGroup) -> bool:
    blob = _title_blob(gtg)
    return any(s in blob for s in EXCLUDED_TITLE_SUBSTRINGS)


def _is_split_genre(gtg: GameTaskGroup) -> bool:
    tg = gtg.task_group
    if tg is None:
        return False
    if tg.view == 'proportions':
        return True
    blob = _title_blob(gtg)
    if SEQUENCES_SUBSTRING in blob:
        return True
    return False


def _major_of_number(number: str) -> Optional[str]:
    raw = re.sub(r'\*', '', str(number or '').strip())
    if not raw:
        return None
    # Crossword-style prefixes etc.: take first digit-run segment.
    parts = raw.replace('Г', '').replace('В', '').split('.')
    head = parts[0].strip()
    m = re.match(r'^(\d+)', head)
    if not m:
        return None
    return m.group(1)


def _playable_tasks(tg: TaskGroup) -> list[Task]:
    tasks = list(tg.tasks.visible())
    playable = [t for t in tasks if t.task_type != 'text_with_forms']
    return playable


def _desyatka_label(game: Game) -> str:
    name = (getattr(game, 'outside_name', None) or game.name or game.id or '').strip()
    return name or str(game.id)


def enumerate_units_for_gtg(gtg: GameTaskGroup) -> list[WeekUnit]:
    """Разбить один круг Десяточки на units (0 если не подходит)."""
    tg = gtg.task_group
    if tg is None:
        return []
    if not DES_GAME_RE.match(str(gtg.game_id)):
        return []
    if _is_excluded_genre(gtg):
        return []
    playable = _playable_tasks(tg)
    if not playable:
        return []

    des_label = _desyatka_label(gtg.game)
    base_kwargs = dict(
        source_game_id=str(gtg.game_id),
        source_task_group_id=tg.pk,
        source_gtg_name=gtg.name or '',
        source_tg_label=tg.label or '',
        source_tg_view=tg.view or 'default',
        desyatka_label=des_label,
    )

    if not _is_split_genre(gtg):
        return [WeekUnit(task_numbers=None, major=None, **base_kwargs)]

    by_major: dict[str, list[str]] = {}
    for task in sorted(playable, key=lambda t: t.key_sort()):
        major = _major_of_number(task.number)
        if major is None:
            continue
        by_major.setdefault(major, []).append(str(task.number))

    if not by_major:
        return [WeekUnit(task_numbers=None, major=None, **base_kwargs)]

    units = []
    for major in sorted(by_major.keys(), key=lambda m: int(m)):
        nums = tuple(by_major[major])
        units.append(WeekUnit(task_numbers=nums, major=major, **base_kwargs))
    return units


def iter_source_gtgs():
    qs = (
        GameTaskGroup.objects.filter(game__project_id=SOURCE_PROJECT_ID)
        .filter(game__id__startswith='des')
        .select_related('game', 'task_group')
        .order_by('game_id', 'number', 'pk')
    )
    for gtg in qs:
        if DES_GAME_RE.match(str(gtg.game_id)):
            yield gtg


def enumerate_all_units() -> list[WeekUnit]:
    units: list[WeekUnit] = []
    for gtg in iter_source_gtgs():
        units.extend(enumerate_units_for_gtg(gtg))
    return units


def scheduled_exclude_keys(*, week_task_game: Game) -> set[tuple[int, frozenset[str] | None]]:
    """Уже поставленные в очередь недели (по provenance tags) + запрещённые."""
    from games.support.services.banned import banned_unit_keys

    keys: set[tuple[int, frozenset[str] | None]] = set()
    links = (
        GameTaskGroup.objects.filter(game=week_task_game)
        .select_related('task_group')
    )
    for link in links:
        tags = (link.task_group.tags or {}) if link.task_group_id else {}
        src = tags.get(WEEK_TASK_SOURCE_TAG) or {}
        if not isinstance(src, dict):
            continue
        tg_id = src.get('task_group_id')
        if tg_id is None:
            continue
        try:
            tg_id_int = int(tg_id)
        except (TypeError, ValueError):
            continue
        nums = src.get('task_numbers')
        if nums is None:
            keys.add((tg_id_int, None))
        else:
            keys.add((tg_id_int, frozenset(str(x) for x in nums)))
    keys |= banned_unit_keys(week_task_game)
    return keys


def pick_random_units(
    n: int,
    *,
    exclude: set[tuple[int, frozenset[str] | None]] | None = None,
    rng: random.Random | None = None,
) -> list[WeekUnit]:
    """Сэмпл: случайная Десяточка → случайный unit; без повторов exclude."""
    if n < 1:
        return []
    r = rng or random.Random()
    exclude = set(exclude or ())
    all_units = [u for u in enumerate_all_units() if u.exclude_key not in exclude]
    if not all_units:
        return []

    by_game: dict[str, list[WeekUnit]] = {}
    for u in all_units:
        by_game.setdefault(u.source_game_id, []).append(u)

    picked: list[WeekUnit] = []
    used = set(exclude)
    available_games = [gid for gid, us in by_game.items() if any(u.exclude_key not in used for u in us)]

    while len(picked) < n and available_games:
        gid = r.choice(available_games)
        candidates = [u for u in by_game[gid] if u.exclude_key not in used]
        if not candidates:
            available_games = [g for g in available_games if g != gid]
            continue
        unit = r.choice(candidates)
        picked.append(unit)
        used.add(unit.exclude_key)
        if not any(u.exclude_key not in used for u in by_game[gid]):
            available_games = [g for g in available_games if g != gid]

    return picked


def _copy_task(old: Task, new_tg: TaskGroup) -> Task:
    old_hints = list(old.hints.all())
    new_task = Task(
        task_group=new_tg,
        number=old.number,
        image=old.image,
        text=old.text,
        checker_data=old.checker_data,
        answer=old.answer,
        answer_comment=old.answer_comment,
        task_type=old.task_type,
        checker=old.checker,
        points=old.points,
        max_attempts=old.max_attempts,
        image_width=old.image_width,
        field_text_width=old.field_text_width,
        tags=dict(old.tags or {}),
        is_removed=old.is_removed,
    )
    new_task.save()
    for hint in old_hints:
        hint.pk = None
        hint.task = new_task
        hint.save()
    return new_task


@transaction.atomic
def materialize_unit(unit: WeekUnit, *, week_number: int, week_task_game: Game) -> GameTaskGroup:
    """Создать снимок TaskGroup + GameTaskGroup для слота недели."""
    source_tg = TaskGroup.objects.filter(pk=unit.source_task_group_id).first()
    if source_tg is None:
        raise ValueError(f'source TaskGroup {unit.source_task_group_id} not found')

    playable = _playable_tasks(source_tg)
    if unit.task_numbers is None:
        tasks_to_copy = playable
        numbers_tag = None
    else:
        want = set(unit.task_numbers)
        tasks_to_copy = [t for t in playable if str(t.number) in want]
        numbers_tag = list(unit.task_numbers)

    if not tasks_to_copy:
        raise ValueError(f'no tasks to clone for unit {unit}')

    provenance = {
        WEEK_TASK_SOURCE_TAG: {
            'game_id': unit.source_game_id,
            'task_group_id': unit.source_task_group_id,
            'task_numbers': numbers_tag,
            'desyatka_label': unit.desyatka_label,
            'source_name': unit.source_gtg_name,
            'major': unit.major,
        }
    }
    tags = dict(source_tg.tags or {})
    tags.update(provenance)

    new_tg = TaskGroup.objects.create(
        label=f'week_task:{week_number}',
        rules=source_tg.rules,
        text=source_tg.text,
        checker=source_tg.checker,
        points=source_tg.points,
        max_attempts=source_tg.max_attempts,
        image_width=source_tg.image_width,
        tags=tags,
        view=source_tg.view,
        is_18_plus=source_tg.is_18_plus,
    )
    for task in sorted(tasks_to_copy, key=lambda t: t.key_sort()):
        _copy_task(task, new_tg)

    link = GameTaskGroup.objects.create(
        game=week_task_game,
        task_group=new_tg,
        number=str(week_number),
        name=unit.display_name(),
    )
    return link


def source_summary_from_tags(tags: Any) -> dict[str, Any]:
    if not isinstance(tags, dict):
        return {}
    src = tags.get(WEEK_TASK_SOURCE_TAG) or {}
    if not isinstance(src, dict):
        return {}
    return src


def source_play_path_from_tags(tags: Any) -> Optional[str]:
    """Относительный URL исходного круга/задания в Десяточке (или None)."""
    src = source_summary_from_tags(tags)
    game_id = (src.get('game_id') or '').strip()
    tg_id = src.get('task_group_id')
    if not game_id or not tg_id:
        return None
    try:
        tg_id_int = int(tg_id)
    except (TypeError, ValueError):
        return None

    link = (
        GameTaskGroup.objects
        .filter(game_id=game_id, task_group_id=tg_id_int)
        .only('number')
        .first()
    )
    if link is None:
        return f'/games/{game_id}/'

    from games.telegram.game_urls import task_group_play_path

    game = Game.objects.filter(pk=game_id).only('id', 'project_id', 'tags').first()
    if game is None:
        path = f'/games/{game_id}/{link.number}/'
    else:
        path = task_group_play_path(game, link.number)

    nums = src.get('task_numbers')
    if isinstance(nums, list) and len(nums) == 1:
        task = (
            Task.objects
            .filter(task_group_id=tg_id_int, number=str(nums[0]), is_removed=False)
            .only('pk')
            .first()
        )
        if task is not None:
            return f'{path}#new-task-{task.pk}'
    return path


def unit_to_dict(unit: WeekUnit) -> dict[str, Any]:
    return {
        'source_game_id': unit.source_game_id,
        'source_task_group_id': unit.source_task_group_id,
        'source_gtg_name': unit.source_gtg_name,
        'desyatka_label': unit.desyatka_label,
        'major': unit.major,
        'task_numbers': list(unit.task_numbers) if unit.task_numbers is not None else None,
        'label': (
            f'п.{unit.major}' if unit.major is not None else 'весь круг'
        ),
        'display_name': unit.display_name(),
    }


def pool_catalog(
    *,
    exclude: set[tuple[int, frozenset[str] | None]] | None = None,
) -> list[dict[str, Any]]:
    """Каталог для админки: десяточка → круги → units (весь круг / подмножество)."""
    exclude = set(exclude or ())
    by_game: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for gtg in iter_source_gtgs():
        units = enumerate_units_for_gtg(gtg)
        if not units:
            continue
        gid = str(gtg.game_id)
        if gid not in by_game:
            by_game[gid] = {
                'game_id': gid,
                'label': _desyatka_label(gtg.game),
                'circles': [],
            }
            order.append(gid)
        circle_units = []
        for u in units:
            d = unit_to_dict(u)
            d['already_scheduled'] = u.exclude_key in exclude
            circle_units.append(d)
        by_game[gid]['circles'].append({
            'task_group_id': gtg.task_group_id,
            'gtg_number': str(gtg.number),
            'gtg_name': gtg.name or (gtg.task_group.label if gtg.task_group_id else '') or '',
            'units': circle_units,
        })

    return [by_game[gid] for gid in order]


def resolve_unit(
    *,
    source_task_group_id: int,
    major: str | None = None,
    task_numbers: list[str] | tuple[str, ...] | None = None,
) -> WeekUnit:
    """Найти WeekUnit в пуле или собрать кастомное подмножество tasks."""
    gtg = (
        GameTaskGroup.objects.filter(
            task_group_id=source_task_group_id,
            game__project_id=SOURCE_PROJECT_ID,
        )
        .filter(game__id__startswith='des')
        .select_related('game', 'task_group')
        .first()
    )
    if gtg is None or not DES_GAME_RE.match(str(gtg.game_id)):
        raise ValueError(f'TaskGroup {source_task_group_id} не из Десяточки')
    if gtg.task_group is None:
        raise ValueError(f'TaskGroup {source_task_group_id} не найден')

    if task_numbers is not None:
        want = tuple(str(x) for x in task_numbers)
        if not want:
            raise ValueError('Пустое подмножество tasks')
        playable = _playable_tasks(gtg.task_group)
        have = {str(t.number) for t in playable}
        missing = [n for n in want if n not in have]
        if missing:
            raise ValueError('Нет playable tasks: {}'.format(', '.join(missing)))
        majors = {_major_of_number(n) for n in want}
        majors.discard(None)
        major_tag = next(iter(majors)) if len(majors) == 1 else None
        return WeekUnit(
            source_game_id=str(gtg.game_id),
            source_task_group_id=gtg.task_group_id,
            source_gtg_name=gtg.name or '',
            source_tg_label=(gtg.task_group.label or ''),
            source_tg_view=gtg.task_group.view or 'default',
            task_numbers=want,
            major=major_tag,
            desyatka_label=_desyatka_label(gtg.game),
        )

    units = enumerate_units_for_gtg(gtg)
    if not units:
        raise ValueError(
            f'Круг TaskGroup {source_task_group_id} не подходит для задания недели'
        )
    major_norm = None if major is None or major == '' else str(major)
    if major_norm is None:
        whole = [u for u in units if u.major is None]
        if len(whole) == 1:
            return whole[0]
        if len(units) == 1:
            return units[0]
        raise ValueError(
            'Для этого круга нужно выбрать подмножество (п.N), не весь круг'
        )
    for u in units:
        if u.major == major_norm:
            return u
    raise ValueError(f'Подмножество п.{major_norm} не найдено в круге')


@transaction.atomic
def rematerialize_link(link: GameTaskGroup, unit: WeekUnit) -> GameTaskGroup:
    """Заменить снимок заданий у существующего слота недели (тот же link/number)."""
    source_tg = TaskGroup.objects.filter(pk=unit.source_task_group_id).first()
    if source_tg is None:
        raise ValueError(f'source TaskGroup {unit.source_task_group_id} not found')
    week_tg = link.task_group
    if week_tg is None:
        raise ValueError('у слота нет TaskGroup')

    playable = _playable_tasks(source_tg)
    if unit.task_numbers is None:
        tasks_to_copy = playable
        numbers_tag = None
    else:
        want = set(unit.task_numbers)
        tasks_to_copy = [t for t in playable if str(t.number) in want]
        numbers_tag = list(unit.task_numbers)
    if not tasks_to_copy:
        raise ValueError(f'no tasks to clone for unit {unit}')

    for old in list(week_tg.tasks.all()):
        old.hints.all().delete()
        old.delete()

    provenance = {
        WEEK_TASK_SOURCE_TAG: {
            'game_id': unit.source_game_id,
            'task_group_id': unit.source_task_group_id,
            'task_numbers': numbers_tag,
            'desyatka_label': unit.desyatka_label,
            'source_name': unit.source_gtg_name,
            'major': unit.major,
        }
    }
    tags = dict(source_tg.tags or {})
    tags.update(provenance)

    week_tg.rules = source_tg.rules
    week_tg.text = source_tg.text
    week_tg.checker = source_tg.checker
    week_tg.points = source_tg.points
    week_tg.max_attempts = source_tg.max_attempts
    week_tg.image_width = source_tg.image_width
    week_tg.tags = tags
    week_tg.view = source_tg.view
    week_tg.is_18_plus = source_tg.is_18_plus
    week_tg.save()

    for task in sorted(tasks_to_copy, key=lambda t: t.key_sort()):
        _copy_task(task, week_tg)

    link.name = unit.display_name()
    link.save(update_fields=['name'])
    return link
