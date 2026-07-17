# Ежедневные лесенки (раздел «Лесенка»): номер круга = 1, 2, 3…
# Дата публикации N-й лесенки хранится в Game.tags['ladder_publish_start'] + (N-1) дней (МСК).

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

LADDER_GAME_ID = 'ladder'
LADDER_PUBLISH_START_TAG = 'ladder_publish_start'
MOSCOW = ZoneInfo('Europe/Moscow')


def _moscow_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=MOSCOW)


def ladder_publish_start(game) -> datetime | None:
    """Полночь МСК первого дня публикации (лесенка №1)."""
    tags = getattr(game, 'tags', None) or {}
    raw = tags.get(LADDER_PUBLISH_START_TAG)
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


def ladder_number_for_date(game, d: date) -> int | None:
    """Номер лесенки для календарного дня d (МСК), или None до старта."""
    start = ladder_publish_start(game)
    if start is None:
        return None
    days = (d - start.date()).days
    if days < 0:
        return None
    return days + 1


def current_ladder_number(game, now: datetime | None = None) -> int | None:
    """Номер «сегодняшней» лесенки по МСК."""
    now = now or timezone.now()
    return ladder_number_for_date(game, now.astimezone(MOSCOW).date())


def ladder_publish_at(game, number: int | str) -> datetime | None:
    """Момент публикации лесенки с данным номером."""
    start = ladder_publish_start(game)
    if start is None:
        return None
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return _moscow_midnight(start.date()) + timedelta(days=n - 1)


def is_ladder_number_published(game, number: int | str, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    pub = ladder_publish_at(game, number)
    if pub is None:
        return True
    return now >= pub


def filter_published_ladder_links(links, game, now: datetime | None = None):
    """GameTaskGroup rows, у которых наступила дата публикации."""
    now = now or timezone.now()
    if hasattr(links, 'filter'):
        published_pks = [
            link.pk for link in links
            if is_ladder_number_published(game, link.number, now)
        ]
        if not published_pks:
            return links.none()
        return links.filter(pk__in=published_pks)
    return [link for link in links if is_ladder_number_published(game, link.number, now)]


def sort_ladder_links_newest_first(links):
    if hasattr(links, 'filter'):
        from games.models import GameTaskGroup
        return GameTaskGroup.order_queryset_by_number(links, reverse=True)
    return sorted(links, key=lambda link: link.key_sort(), reverse=True)


def visible_ladder_links(links, game, *, reverse=False, now: datetime | None = None):
    """Уже вышедшие лесенки; reverse=True — новые сверху (архив), False — по порядку (результаты)."""
    published = filter_published_ladder_links(links, game, now)
    from games.models import GameTaskGroup
    if hasattr(published, 'filter'):
        return GameTaskGroup.order_queryset_by_number(published, reverse=reverse)
    return GameTaskGroup.sorted_links(published, reverse=reverse)


def get_ladder_hub_context(game, *, published_numbers: set[str] | None = None, now=None):
    """
    Контекст для плитки «Лесенка» на главной.
    published_numbers — номера кругов, уже заведённые в БД (строки).
    """
    now = now or timezone.now()
    today_num = current_ladder_number(game, now)
    start = ladder_publish_start(game)

    cta_number = None
    is_today = False
    cta_label = ''
    status = 'coming_soon'

    if today_num is not None and published_numbers and str(today_num) in published_numbers:
        cta_number = str(today_num)
        is_today = True
        cta_label = 'Сегодняшняя лесенка'
        status = 'today'
    elif published_numbers:
        published_ints = sorted(int(n) for n in published_numbers if str(n).isdigit())
        visible = [n for n in published_ints if is_ladder_number_published(game, n, now)]
        if visible:
            latest = visible[-1]
            cta_number = str(latest)
            if today_num is not None and latest == today_num:
                is_today = True
                cta_label = 'Сегодняшняя лесенка'
                status = 'today'
            else:
                cta_label = 'Последняя лесенка'
                status = 'latest'
    elif today_num is not None and start and now < ladder_publish_at(game, 1):
        status = 'coming_soon'

    play_url = f'/games/{LADDER_GAME_ID}/{cta_number}/' if cta_number else None
    section_url = f'/games/{LADDER_GAME_ID}/'

    today_label = None
    if today_num is not None:
        today_label = f'№{today_num}'

    return {
        'ladder_game': game,
        'ladder_cta_number': cta_number,
        'ladder_cta_label': cta_label,
        'ladder_is_today': is_today,
        'ladder_play_url': play_url,
        'ladder_section_url': section_url,
        'ladder_status': status,
        'ladder_today_label': today_label,
        'ladder_publish_start': start,
    }
