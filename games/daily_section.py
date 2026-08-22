"""Общее расписание ежедневных / еженедельных разделов.

Лесенка, алфавитка, салат и задание недели отличаются тегом старта, шагом
дней и парой UX-флагов. Календарь, эмбарго и CTA на главной — одни.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

MOSCOW = ZoneInfo('Europe/Moscow')


def moscow_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=MOSCOW)


def parse_publish_start(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        s = str(raw).strip()
        if not s:
            return None
        if 'T' not in s and len(s) == 10:
            s = s + 'T00:00:00+03:00'
        dt = datetime.fromisoformat(s)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, MOSCOW)
    return dt.astimezone(MOSCOW)


@dataclass(frozen=True)
class DailySchedule:
    game_id: str
    publish_start_tag: str
    step_days: int = 1
    open_without_start: bool = False
    empty_hub_status: str = 'coming_soon'
    coming_soon_before_first: bool = False
    today_label_when: str = 'has_number'
    cta_today: str = ''
    cta_latest: str = ''
    daily_play_layout: bool = True

    def publish_start(self, game) -> datetime | None:
        tags = getattr(game, 'tags', None) or {}
        return parse_publish_start(tags.get(self.publish_start_tag))

    def number_for_date(self, game, d: date) -> int | None:
        start = self.publish_start(game)
        if start is None:
            return None
        days = (d - start.date()).days
        if days < 0:
            return None
        return days // self.step_days + 1

    def current_number(self, game, now: datetime | None = None) -> int | None:
        now = now or timezone.now()
        return self.number_for_date(game, now.astimezone(MOSCOW).date())

    def publish_at(self, game, number: int | str) -> datetime | None:
        start = self.publish_start(game)
        if start is None:
            return None
        try:
            n = int(number)
        except (TypeError, ValueError):
            return None
        if n < 1:
            return None
        return moscow_midnight(start.date()) + timedelta(days=self.step_days * (n - 1))

    def is_published(self, game, number: int | str, now: datetime | None = None) -> bool:
        now = now or timezone.now()
        pub = self.publish_at(game, number)
        if pub is None:
            return self.open_without_start
        return now >= pub

    def filter_published(self, links, game, now: datetime | None = None):
        now = now or timezone.now()
        if hasattr(links, 'filter'):
            published_pks = [
                link.pk for link in links
                if self.is_published(game, link.number, now)
            ]
            if not published_pks:
                return links.none()
            return links.filter(pk__in=published_pks)
        return [link for link in links if self.is_published(game, link.number, now)]

    def visible_links(self, links, game, *, reverse=False, now: datetime | None = None):
        published = self.filter_published(links, game, now)
        from games.models import GameTaskGroup
        if hasattr(published, 'filter'):
            return GameTaskGroup.order_queryset_by_number(published, reverse=reverse)
        return GameTaskGroup.sorted_links(published, reverse=reverse)

    def hub_context(self, game, *, published_numbers: set[str] | None = None, now=None):
        now = now or timezone.now()
        today_num = self.current_number(game, now)
        start = self.publish_start(game)

        cta_number = None
        is_today = False
        cta_label = ''
        status = self.empty_hub_status

        if today_num is not None and published_numbers and str(today_num) in published_numbers:
            cta_number = str(today_num)
            is_today = True
            cta_label = self.cta_today
            status = 'today'
        elif published_numbers:
            published_ints = sorted(int(n) for n in published_numbers if str(n).isdigit())
            visible = [n for n in published_ints if self.is_published(game, n, now)]
            if visible:
                latest = visible[-1]
                cta_number = str(latest)
                if today_num is not None and latest == today_num:
                    is_today = True
                    cta_label = self.cta_today
                    status = 'today'
                else:
                    cta_label = self.cta_latest
                    status = 'latest'
        elif self.coming_soon_before_first:
            first = self.publish_at(game, 1)
            if start is None or (first is not None and now < first):
                status = 'coming_soon'

        from games.section_paths import section_hub_path, section_last_path
        play_url = section_last_path(self.game_id) if cta_number else None
        section_url = section_hub_path(self.game_id)

        today_label = None
        if today_num is not None:
            if self.today_label_when == 'today_only':
                if is_today:
                    today_label = f'№{today_num}'
            else:
                today_label = f'№{today_num}'

        prefix = self.game_id
        return {
            f'{prefix}_game': game,
            f'{prefix}_cta_number': cta_number,
            f'{prefix}_cta_label': cta_label,
            f'{prefix}_is_today': is_today,
            f'{prefix}_play_url': play_url,
            f'{prefix}_section_url': section_url,
            f'{prefix}_status': status,
            f'{prefix}_today_label': today_label,
            f'{prefix}_publish_start': start,
        }


LADDER_SCHEDULE = DailySchedule(
    game_id='ladder',
    publish_start_tag='ladder_publish_start',
    open_without_start=True,
    cta_today='Сегодняшняя лесенка',
    cta_latest='Последняя лесенка',
)

ALPHABETTY_SCHEDULE = DailySchedule(
    game_id='alphabetty',
    publish_start_tag='alphabetty_publish_start',
    cta_today='Сегодняшняя алфавитка',
    cta_latest='Последняя алфавитка',
    daily_play_layout=False,
)

WORD_SALAD_SCHEDULE = DailySchedule(
    game_id='word_salad',
    publish_start_tag='word_salad_publish_start',
    cta_today='Сегодняшний салат',
    cta_latest='Последний салат',
)

WEEK_TASK_SCHEDULE = DailySchedule(
    game_id='week_task',
    publish_start_tag='week_task_publish_start',
    step_days=7,
    empty_hub_status='empty',
    coming_soon_before_first=True,
    today_label_when='today_only',
    cta_today='Задание этой недели',
    cta_latest='Последнее задание недели',
)

SCHEDULES: dict[str, DailySchedule] = {
    LADDER_SCHEDULE.game_id: LADDER_SCHEDULE,
    ALPHABETTY_SCHEDULE.game_id: ALPHABETTY_SCHEDULE,
    WORD_SALAD_SCHEDULE.game_id: WORD_SALAD_SCHEDULE,
    WEEK_TASK_SCHEDULE.game_id: WEEK_TASK_SCHEDULE,
}


def schedule_for(game_id) -> DailySchedule | None:
    if not game_id:
        return None
    return SCHEDULES.get(str(game_id))


def is_scheduled_game(game_id) -> bool:
    return schedule_for(game_id) is not None


def uses_daily_play_layout(game_id) -> bool:
    sched = schedule_for(game_id)
    return bool(sched and sched.daily_play_layout)


def scheduled_number_is_public(game, number, now: datetime | None = None) -> bool:
    """True, если номер уже вышел (или у игры нет расписания)."""
    sched = schedule_for(getattr(game, 'id', None))
    if sched is None:
        return True
    return sched.is_published(game, number, now)


def publish_at_for(game, number):
    sched = schedule_for(getattr(game, 'id', None))
    if sched is None:
        return None
    return sched.publish_at(game, number)


def current_number_for(game, now: datetime | None = None) -> int | None:
    sched = schedule_for(getattr(game, 'id', None))
    if sched is None:
        return None
    return sched.current_number(game, now)


def filter_published_links(links, game, now: datetime | None = None):
    sched = schedule_for(getattr(game, 'id', None))
    if sched is None:
        return links
    return sched.filter_published(links, game, now)


def visible_links(links, game, *, reverse=False, now: datetime | None = None):
    sched = schedule_for(getattr(game, 'id', None))
    if sched is None:
        from games.models import GameTaskGroup
        if hasattr(links, 'filter'):
            return GameTaskGroup.order_queryset_by_number(links, reverse=reverse)
        return GameTaskGroup.sorted_links(links, reverse=reverse)
    return sched.visible_links(links, game, reverse=reverse, now=now)
