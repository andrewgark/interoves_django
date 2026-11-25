"""Support console для простых ежедневных игр (адвент-календарь, криптики).

Каждый день — одна группа с одним обычным заданием. Чем отличаются разделы
(поля формы, как они ложатся в Task, чекер), описывает ``SimpleDailySection``
в ``games.console.games.simple_daily_sections``; расписание, настройки и
права общие.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

from django.db import transaction
from django.utils import timezone

from games.console.http.schedule import (
    assert_future_only_order,
    build_schedule_page_context,
    defer_future_slot,
    delete_future_slot,
    effective_schedule_number,
    renumber_links,
    restore_deferred_slot,
    save_publish_start_date,
    shift_links,
)
from games.daily.catalog.simple_daily import SECTION_DESCRIPTION_TAG, SHOW_ANSWER_TAG, day_count
from games.daily.page.section import BoundedDailySchedule
from games.models import Audio, CheckerType, Game, GameTaskGroup, Image, Task, TaskGroup
from games.sections.paths import section_hub_path, section_play_path

DAY_POINTS = 1
# Text author of a day, in Task.tags like the salad and ladder offers.
AUTHOR_FIELD = 'author'
IMAGE_MAX_BYTES = 10 * 1024 * 1024
AUDIO_MAX_BYTES = 20 * 1024 * 1024
# No SVG: opened directly from /media/ it would run scripts on our domain.
_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
_AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.ogg', '.wav'}


class SimpleDailyError(Exception):
    """Ошибка операции с расписанием простой ежедневной игры."""


@dataclass(frozen=True)
class DayField:
    """Одно поле формы дня в консоли."""

    key: str
    label: str
    kind: str = 'input'  # input | textarea | html
    rows: int = 3
    hint: str = ''
    placeholder: str = ''
    required: bool = False
    # Кнопки загрузки картинки и mp3, которые вставляют HTML в это поле.
    media: bool = False
    # Моноширинный шрифт: служебная разметка, формулы.
    mono: bool = False


@dataclass(frozen=True)
class TaskContent:
    """Что из формы дня попадает в Task."""

    text: str
    checker_data: str
    answer: str
    answer_comment: str = ''
    tags: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SimpleDailySection:
    game_id: str
    schedule: BoundedDailySchedule
    console_group: str
    title: str
    checker_id: str
    day_name: str  # «День {n}»
    lead: str
    fields: tuple[DayField, ...]
    # Поля формы (строки) → TaskContent; ошибки ввода — SimpleDailyError.
    to_task: Callable[[dict[str, str]], TaskContent]
    # Task → поля формы.
    from_task: Callable[[Task], dict[str, str]]
    # Строка под названием дня в списке.
    summary: Callable[[Task], str]
    # Автор каждого нового дня (User.id); без профиля у этого пользователя — без автора.
    default_author_user_id: int | None = None
    # Как назвать автора по умолчанию в подсказке поля «Автор».
    default_author_label: str = 'автор раздела'
    # Живой предпросмотр под полями: поля формы → {'html': …, 'errors': […]}.
    preview: Optional[Callable[[dict[str, str]], dict[str, Any]]] = None
    preview_title: str = 'Как это увидят игроки'
    # Шаблон справки, которая открывается ссылкой над предпросмотром.
    help_template: str = ''
    help_label: str = ''

    @property
    def has_preview(self) -> bool:
        # Templates call callables, so they test this flag, not ``preview``.
        return self.preview is not None

    def auto_name(self, number: int) -> str:
        return self.day_name.format(n=number)

    def is_auto_name(self, name: str) -> bool:
        pattern = re.escape(self.day_name).replace(re.escape('{n}'), r'\d+')
        return bool(re.fullmatch(pattern, name or ''))


@dataclass(frozen=True)
class DayRow:
    link_id: int
    task_group_id: int
    number: int
    name: str
    publish_date: Optional[str]
    is_published: bool
    is_today: bool
    is_after_end: bool
    summary: str
    play_url: str
    site_url: str
    is_deferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SimpleDailyConsole:
    """Расписание и настройки одной простой ежедневной игры."""

    def __init__(self, section: SimpleDailySection):
        self.section = section
        self.schedule = section.schedule

    # --- game & links -------------------------------------------------

    def game(self) -> Game:
        try:
            return Game.objects.get(pk=self.section.game_id)
        except Game.DoesNotExist as exc:
            raise SimpleDailyError('Игра {} не найдена'.format(self.section.game_id)) from exc

    def _locked_game(self) -> Game:
        return Game.objects.select_for_update().get(pk=self.game().pk)

    def _link(self, game: Game, link_id: int, *, lock: bool = False) -> GameTaskGroup:
        qs = GameTaskGroup.objects.filter(game=game, pk=link_id).select_related('task_group')
        if lock:
            qs = qs.select_for_update()
        link = qs.first()
        if link is None:
            raise SimpleDailyError('День не найден')
        return link

    @staticmethod
    def _task(link: GameTaskGroup) -> Task | None:
        if not link.task_group_id:
            return None
        return (
            Task.objects.filter(task_group_id=link.task_group_id, is_removed=False)
            .order_by('pk')
            .first()
        )

    def _site_url(self, link: GameTaskGroup, number: int) -> str:
        # The hash opens the day before its date, so it doubles as a preview link.
        return section_play_path(self.section.game_id, link.share_hash or number)

    def _publish_date(self, game: Game, number: int) -> Optional[str]:
        pub = self.schedule.publish_at(game, number)
        return pub.date().isoformat() if pub else None

    def _sync_links(self, links: list[GameTaskGroup], new_numbers: list[int]) -> None:
        task_groups = []
        for link, new_num in zip(links, new_numbers):
            tg = link.task_group
            tg.label = '{}:{}'.format(self.section.game_id, new_num)
            task_groups.append(tg)
            if not (link.name or '').strip() or self.section.is_auto_name(link.name):
                link.name = self.section.auto_name(new_num)
        if task_groups:
            TaskGroup.objects.bulk_update(task_groups, ['label'])

    def _renumber(self, ordered_links: list[GameTaskGroup]) -> None:
        renumber_links(ordered_links, sync_links=self._sync_links)

    # --- rows ----------------------------------------------------------

    def rows(self, *, now: datetime | None = None) -> list[DayRow]:
        game = self.game()
        now = now or timezone.now()
        today_num = self.schedule.current_number(game, now)
        count = day_count(game)
        links = GameTaskGroup.sorted_links(
            GameTaskGroup.objects.filter(game=game).select_related('task_group'),
            reverse=False, include_deferred=True,
        )
        tasks = {}
        for task in Task.objects.filter(
            task_group_id__in=[link.task_group_id for link in links if link.task_group_id],
            is_removed=False,
        ).order_by('-pk'):
            tasks[task.task_group_id] = task
        rows = []
        for link in links:
            number = effective_schedule_number(link)
            if number is None:
                continue
            is_pub = False if link.is_deferred else self.schedule.is_published(game, number, now)
            task = tasks.get(link.task_group_id)
            rows.append(DayRow(
                link_id=link.pk,
                task_group_id=link.task_group_id,
                number=number,
                name=link.name or self.section.auto_name(number),
                publish_date=self._publish_date(game, number),
                is_published=is_pub,
                is_today=bool(today_num is not None and number == today_num and is_pub),
                is_after_end=(
                    not link.is_deferred and count is not None and number > count
                ),
                summary=self.section.summary(task) if task else '',
                play_url=section_play_path(self.section.game_id, number),
                site_url=self._site_url(link, number),
                is_deferred=link.is_deferred,
            ))
        return rows

    def last_published_number(self, *, now: datetime | None = None) -> int:
        published = [r.number for r in self.rows(now=now) if r.is_published]
        return max(published) if published else 0

    def detail(self, link_id: int) -> dict[str, Any]:
        game = self.game()
        link = self._link(game, link_id)
        number = effective_schedule_number(link) or 0
        task = self._task(link)
        return {
            'link_id': link.pk,
            'task_group_id': link.task_group_id,
            'number': number,
            'name': link.name or self.section.auto_name(number),
            'publish_date': self._publish_date(game, number),
            'is_published': (
                False if link.is_deferred else self.schedule.is_published(game, number)
            ),
            'is_deferred': link.is_deferred,
            'fields': self._fields_for(task) if task else {},
            'play_url': section_play_path(self.section.game_id, number),
            'site_url': self._site_url(link, number),
        }

    # --- days ----------------------------------------------------------

    def _default_author(self):
        from games.models import Profile

        user_id = self.section.default_author_user_id
        if not user_id:
            return None
        return Profile.objects.filter(user_id=user_id).first()

    def _clean_name(self, raw, number: int) -> str:
        name = str(raw or '').strip()[:200]
        return name or self.section.auto_name(number)

    def day_fields(self) -> tuple[DayField, ...]:
        """Поля раздела и общее поле текстового автора."""
        return (*self.section.fields, DayField(
            AUTHOR_FIELD, 'Автор, если не {}'.format(self.section.default_author_label),
            placeholder='Пусто: {}'.format(self.section.default_author_label),
            hint='Покажем на странице дня. Тогда автором по умолчанию день не считается.',
        ))

    def _content(self, fields: dict[str, str]) -> TaskContent:
        for day_field in self.section.fields:
            if day_field.required and not str(fields.get(day_field.key) or '').strip():
                raise SimpleDailyError('Заполните поле «{}»'.format(day_field.label))
        return self.section.to_task(fields)

    @staticmethod
    def _author_text(fields: dict[str, str]) -> str:
        return ' '.join(str(fields.get(AUTHOR_FIELD) or '').split())[:200]

    def _sync_authors(self, task_group: TaskGroup, author_text: str) -> None:
        """The section's default author, unless the day names someone else."""
        default = self._default_author()
        if default is None:
            return
        if author_text:
            task_group.authors.remove(default)
        elif not task_group.authors.exists():
            task_group.authors.add(default)

    def _fields_for(self, task: Task) -> dict[str, str]:
        return {
            **self.section.from_task(task),
            AUTHOR_FIELD: str((task.tags or {}).get(AUTHOR_FIELD) or ''),
        }

    @transaction.atomic
    def create(self, *, at_number: int, name: str | None = None,
               fields: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
        """Вставить день; автором группы записывается автор раздела по умолчанию."""
        fields = fields or {}
        content = self._content(fields)
        author_text = self._author_text(fields)
        if at_number < 1:
            raise SimpleDailyError('Номер должен быть >= 1')
        locked_until = self.last_published_number(now=now)
        if at_number <= locked_until:
            raise SimpleDailyError(
                'Нельзя вставлять среди уже вышедших дней (доступно с №{})'.format(locked_until + 1)
            )
        game = self._locked_game()
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
                sync_link=lambda link, new_num: self._sync_links([link], [new_num]),
            )

        checker = CheckerType.objects.get(id=self.section.checker_id)
        task_group = TaskGroup.objects.create(
            label='{}:{}'.format(self.section.game_id, at_number),
            checker=checker,
            points=DAY_POINTS,
            max_attempts=None,
        )
        self._sync_authors(task_group, author_text)
        Task.objects.create(
            task_group=task_group,
            number='1',
            task_type='default',
            checker=checker,
            checker_data=content.checker_data,
            answer=content.answer,
            answer_comment=content.answer_comment or None,
            text=content.text,
            tags={**content.tags, **({AUTHOR_FIELD: author_text} if author_text else {})},
            points=DAY_POINTS,
            max_attempts=None,
            is_removed=False,
        )
        link = GameTaskGroup.objects.create(
            game=game,
            task_group=task_group,
            number=str(at_number),
            name=self._clean_name(name, at_number),
        )
        return self.detail(link.pk)

    @transaction.atomic
    def update(self, link_id: int, *, name: str | None = None,
               fields: dict[str, str] | None = None) -> dict[str, Any]:
        """Править день. Новые варианты ответа перепроверяют уже отправленные."""
        game = self.game()
        link = self._link(game, link_id, lock=True)
        number = effective_schedule_number(link) or 0
        if name is not None:
            link.name = self._clean_name(name, number)
            link.save(update_fields=['name'])
        if fields is None:
            return self.detail(link.pk)
        task = self._task(link)
        if task is None:
            raise SimpleDailyError('У дня нет задания')
        merged = {**self._fields_for(task), **fields}
        content = self._content(merged)
        author_text = self._author_text(merged)
        checker_changed = (
            content.checker_data != (task.checker_data or '')
            or task.checker_id != self.section.checker_id
        )
        task.text = content.text
        task.checker_data = content.checker_data
        task.checker_id = self.section.checker_id
        task.answer = content.answer
        task.answer_comment = content.answer_comment or None
        tags = {**(task.tags or {}), **content.tags}
        if author_text:
            tags[AUTHOR_FIELD] = author_text
        else:
            tags.pop(AUTHOR_FIELD, None)
        task.tags = tags
        task.save()
        if link.task_group_id:
            self._sync_authors(link.task_group, author_text)
        if checker_changed:
            _recheck_task_after_commit(task.pk)
        return self.detail(link.pk)

    @transaction.atomic
    def reorder(self, ordered_link_ids: list[int], *, now: datetime | None = None) -> list[DayRow]:
        game = self.game()
        if not ordered_link_ids:
            raise SimpleDailyError('Пустой порядок')
        if len(set(ordered_link_ids)) != len(ordered_link_ids):
            raise SimpleDailyError('Дубликаты id в порядке')
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
            raise SimpleDailyError('Список дней устарел, обновите страницу и повторите')
        assert_future_only_order(
            ordered_link_ids,
            self.rows(now=now),
            error_cls=SimpleDailyError,
            published_msg='Нельзя менять порядок уже вышедших дней (№1–{number}). '
            'Переставляйте только будущие.',
        )
        self._renumber([by_id[pk] for pk in ordered_link_ids])
        return self.rows(now=now)

    def delete(self, link_id: int, *, now: datetime | None = None) -> list[DayRow]:
        return delete_future_slot(
            game=self.game(),
            link_id=link_id,
            is_number_published=self.schedule.is_published,
            renumber_links=self._renumber,
            list_rows=self.rows,
            error_cls=SimpleDailyError,
            not_found_msg='День не найден',
            published_msg='Нельзя удалять уже вышедший день №{number}',
            now=now,
        )

    def defer(self, link_id: int, *, now=None) -> list[DayRow]:
        return defer_future_slot(
            game=self.game(), link_id=link_id,
            is_number_published=self.schedule.is_published,
            renumber_links=self._renumber, list_rows=self.rows,
            error_cls=SimpleDailyError, not_found_msg='День не найден',
            published_msg='Нельзя откладывать уже вышедший день №{number}', now=now,
        )

    def restore(self, link_id: int, *, now=None) -> list[DayRow]:
        return restore_deferred_slot(
            game=self.game(), link_id=link_id,
            renumber_links=self._renumber, list_rows=self.rows,
            error_cls=SimpleDailyError, not_found_msg='Отложенный день не найден', now=now,
        )

    # --- settings ------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        game = self.game()
        start = self.schedule.publish_start(game)
        end = self.schedule.end_date(game)
        tags = game.tags or {}
        return {
            'title': game.outside_name or game.name or self.section.title,
            'description': str(tags.get(SECTION_DESCRIPTION_TAG) or ''),
            'start_date': start.date().isoformat() if start else '',
            'end_date': end.isoformat() if end else '',
            'day_count': day_count(game),
            'show_answer': bool(tags.get(SHOW_ANSWER_TAG)),
            'hub_url': section_hub_path(self.section.game_id),
        }

    @transaction.atomic
    def save_settings(self, *, title: str, description: str, start_date: str,
                      end_date: str = '', show_answer: bool = False) -> dict[str, Any]:
        """Сохранить название, описание и даты. Вышедшие дни не сдвигаются."""
        game = self._locked_game()
        title = str(title or '').strip()
        if not title:
            raise SimpleDailyError('Нужно название')
        start = _parse_date(start_date, 'первый день')
        end = _parse_date(end_date, 'последний день')
        if start is not None and end is not None and end < start:
            raise SimpleDailyError('Последний день раньше первого')

        old_start = self.schedule.publish_start(game)
        old_start_date = old_start.date() if old_start else None
        if old_start_date != start:
            published = [r.number for r in self.rows() if r.is_published]
            if published:
                raise SimpleDailyError(
                    'Уже вышли дни №1–{}: дату первого дня менять нельзя'.format(max(published))
                )

        if start is not None:
            save_publish_start_date(
                game, self.schedule.publish_start_tag, start.isoformat(),
                error_cls=SimpleDailyError,
            )
            game.refresh_from_db(fields=['tags'])
        tags = dict(game.tags or {})
        if start is None:
            tags.pop(self.schedule.publish_start_tag, None)
        _set_tag(tags, self.schedule.end_date_tag, end.isoformat() if end else '')
        _set_tag(tags, SECTION_DESCRIPTION_TAG, str(description or '').strip())
        _set_tag(tags, SHOW_ANSWER_TAG, True if show_answer else '')
        game.tags = tags
        game.name = title
        game.outside_name = title
        game.save(update_fields=['tags', 'name', 'outside_name'])
        return self.settings()

    # --- media ---------------------------------------------------------

    def upload_media(self, uploaded) -> dict[str, str]:
        """Сохранить картинку (Image) или звук (Audio) и вернуть HTML для вставки."""
        name = str(getattr(uploaded, 'name', '') or 'file')
        ext = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
        content_type = str(getattr(uploaded, 'content_type', '') or '')
        if not ext and content_type:
            import mimetypes

            ext = mimetypes.guess_extension(content_type.split(';')[0].strip()) or ''
        size = int(getattr(uploaded, 'size', 0) or 0)
        media_id = '{}-{}'.format(self.section.game_id, uuid.uuid4().hex[:12])
        title = name.rsplit('.', 1)[0].strip() or 'Аудио'
        if ext == '.svg' or 'svg' in content_type:
            raise SimpleDailyError('SVG загрузить нельзя: сохраните картинку как PNG')
        if ext in _IMAGE_EXTENSIONS or content_type.startswith('image/'):
            if size > IMAGE_MAX_BYTES:
                raise SimpleDailyError('Картинка больше 10 МБ')
            image = Image(id=media_id)
            image.image.save('{}/{}{}'.format(self.section.game_id, media_id, ext or '.png'), uploaded, save=False)
            image.save()
            url = image.image.url
            return {
                'kind': 'image',
                'id': media_id,
                'url': url,
                'html': '<img src="{}" alt="">'.format(url),
            }
        if ext in _AUDIO_EXTENSIONS or content_type.startswith('audio/'):
            if size > AUDIO_MAX_BYTES:
                raise SimpleDailyError('Аудиофайл больше 20 МБ')
            audio = Audio(id=media_id, title=title)
            audio.audio.save('{}/{}{}'.format(self.section.game_id, media_id, ext or '.mp3'), uploaded, save=False)
            audio.save()
            url = audio.audio.url
            safe_title = _attr(title)
            return {
                'kind': 'audio',
                'id': media_id,
                'url': url,
                'html': (
                    '<button type="button" class="new-btn new-btn--ghost new-btn--mini" '
                    'audio-url="{url}" audio-title="{title}">'
                    '<i class="ph ph-play" aria-hidden="true"></i> {title}</button>'
                ).format(url=url, title=safe_title),
            }
        raise SimpleDailyError('Можно загрузить картинку (png, jpg, gif, webp) или звук (mp3, m4a, ogg, wav)')

    # --- page ----------------------------------------------------------

    def dashboard_context(self, *, now: datetime | None = None) -> dict[str, Any]:
        rows = self.rows(now=now)
        settings = self.settings()
        ctx = build_schedule_page_context(
            rows,
            title=settings['title'],
            prefix=self.section.game_id,
            list_label='Дни раздела',
            publish_start=settings['start_date'] or None,
        )
        ctx.update({
            'section': self.section,
            'rows': rows,
            'rows_json': [row.to_dict() for row in rows],
            'section_settings': settings,
        })
        return ctx


def _attr(value: str) -> str:
    from django.utils.html import escape

    return escape(value)


def _set_tag(tags: dict, key: str, value) -> None:
    if value in ('', None, False):
        tags.pop(key, None)
    else:
        tags[key] = value


def _parse_date(raw, label: str) -> date | None:
    value = str(raw or '').strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SimpleDailyError('Некорректная дата: {}'.format(label)) from exc


def _recheck_task_after_commit(task_id: int) -> None:
    def run():
        from games.ops.workers.jobs.recheck import recheck_full

        task = Task.objects.filter(pk=task_id).first()
        if task is not None:
            recheck_full(None, task=task)

    transaction.on_commit(run, robust=True)
