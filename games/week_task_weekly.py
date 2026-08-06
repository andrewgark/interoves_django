# Еженедельное «Задание недели»: номер = 1, 2, 3…
# Дата публикации N = Game.tags['week_task_publish_start'] + (N−1)*7 дней (понедельник 00:00 МСК).

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

WEEK_TASK_GAME_ID = 'week_task'
WEEK_TASK_PUBLISH_START_TAG = 'week_task_publish_start'
WEEK_TASK_BUFFER_WEEKS = 8
MOSCOW = ZoneInfo('Europe/Moscow')


def _moscow_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=MOSCOW)


def week_task_publish_start(game) -> datetime | None:
    """Полночь МСК понедельника публикации задания №1."""
    tags = getattr(game, 'tags', None) or {}
    raw = tags.get(WEEK_TASK_PUBLISH_START_TAG)
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


def week_task_number_for_date(game, d: date) -> int | None:
    """Номер задания недели для календарного дня d (МСК), или None до старта."""
    start = week_task_publish_start(game)
    if start is None:
        return None
    days = (d - start.date()).days
    if days < 0:
        return None
    return days // 7 + 1


def current_week_task_number(game, now: datetime | None = None) -> int | None:
    """Номер «текущей» недели по МСК."""
    now = now or timezone.now()
    return week_task_number_for_date(game, now.astimezone(MOSCOW).date())


def week_task_publish_at(game, number: int | str) -> datetime | None:
    """Момент публикации задания с данным номером (понедельник 00:00 МСК)."""
    start = week_task_publish_start(game)
    if start is None:
        return None
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return _moscow_midnight(start.date()) + timedelta(days=7 * (n - 1))


def is_week_task_number_published(game, number: int | str, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    pub = week_task_publish_at(game, number)
    # Без publish_start / невалидного номера не открываем буфер.
    if pub is None:
        return False
    return now >= pub


def filter_published_week_task_links(links, game, now: datetime | None = None):
    """GameTaskGroup rows, у которых наступила дата публикации."""
    now = now or timezone.now()
    if hasattr(links, 'filter'):
        published_pks = [
            link.pk for link in links
            if is_week_task_number_published(game, link.number, now)
        ]
        if not published_pks:
            return links.none()
        return links.filter(pk__in=published_pks)
    return [link for link in links if is_week_task_number_published(game, link.number, now)]


def visible_week_task_links(links, game, *, reverse=False, now: datetime | None = None):
    published = filter_published_week_task_links(links, game, now)
    from games.models import GameTaskGroup
    if hasattr(published, 'filter'):
        return GameTaskGroup.order_queryset_by_number(published, reverse=reverse)
    return GameTaskGroup.sorted_links(published, reverse=reverse)


def get_week_task_hub_context(game, *, published_numbers: set[str] | None = None, now=None):
    """Контекст для плитки «Задание недели» на главной."""
    now = now or timezone.now()
    today_num = current_week_task_number(game, now)
    start = week_task_publish_start(game)

    cta_number = None
    is_today = False
    cta_label = ''
    status = 'empty'

    if today_num is not None and published_numbers and str(today_num) in published_numbers:
        cta_number = str(today_num)
        is_today = True
        cta_label = 'Задание этой недели'
        status = 'today'
    elif published_numbers:
        published_ints = sorted(int(n) for n in published_numbers if str(n).isdigit())
        visible = [n for n in published_ints if is_week_task_number_published(game, n, now)]
        if visible:
            latest = visible[-1]
            cta_number = str(latest)
            if today_num is not None and latest == today_num:
                is_today = True
                cta_label = 'Задание этой недели'
                status = 'today'
            else:
                cta_label = 'Последнее задание недели'
                status = 'latest'
    elif start is None or (start and now < week_task_publish_at(game, 1)):
        # До первой публикации (или без даты старта) — «скоро».
        status = 'coming_soon'

    from games.section_paths import section_hub_path, section_play_path

    play_url = section_play_path(WEEK_TASK_GAME_ID, cta_number) if cta_number else None
    section_url = section_hub_path(WEEK_TASK_GAME_ID)

    today_label = f'№{today_num}' if is_today and today_num is not None else None

    return {
        'week_task_game': game,
        'week_task_cta_number': cta_number,
        'week_task_cta_label': cta_label,
        'week_task_is_today': is_today,
        'week_task_play_url': play_url,
        'week_task_section_url': section_url,
        'week_task_status': status,
        'week_task_today_label': today_label,
        'week_task_publish_start': start,
    }
