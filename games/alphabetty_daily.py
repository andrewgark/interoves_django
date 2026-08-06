# Ежедневная Алфавитка: номер = 1, 2, 3…
# Дата публикации N = Game.tags['alphabetty_publish_start'] + (N−1) дней (МСК).

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

ALPHABETTY_GAME_ID = 'alphabetty'
ALPHABETTY_PUBLISH_START_TAG = 'alphabetty_publish_start'
ALPHABETTY_BUFFER_DAYS = 30
MOSCOW = ZoneInfo('Europe/Moscow')


def _moscow_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=MOSCOW)


def alphabetty_publish_start(game) -> datetime | None:
    tags = getattr(game, 'tags', None) or {}
    raw = tags.get(ALPHABETTY_PUBLISH_START_TAG)
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


def alphabetty_number_for_date(game, d: date) -> int | None:
    start = alphabetty_publish_start(game)
    if start is None:
        return None
    days = (d - start.date()).days
    if days < 0:
        return None
    return days + 1


def current_alphabetty_number(game, now: datetime | None = None) -> int | None:
    now = now or timezone.now()
    return alphabetty_number_for_date(game, now.astimezone(MOSCOW).date())


def alphabetty_publish_at(game, number: int | str) -> datetime | None:
    start = alphabetty_publish_start(game)
    if start is None:
        return None
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return _moscow_midnight(start.date()) + timedelta(days=n - 1)


def is_alphabetty_number_published(game, number: int | str, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    pub = alphabetty_publish_at(game, number)
    # Без publish_start не открываем буфер — иначе все будущие слоты станут играбельны.
    if pub is None:
        return False
    return now >= pub


def filter_published_alphabetty_links(links, game, now: datetime | None = None):
    now = now or timezone.now()
    if hasattr(links, 'filter'):
        published_pks = [
            link.pk for link in links
            if is_alphabetty_number_published(game, link.number, now)
        ]
        if not published_pks:
            return links.none()
        return links.filter(pk__in=published_pks)
    return [link for link in links if is_alphabetty_number_published(game, link.number, now)]


def visible_alphabetty_links(links, game, *, reverse=False, now: datetime | None = None):
    """Уже вышедшие алфавитки; reverse=True — новые сверху (архив)."""
    published = filter_published_alphabetty_links(links, game, now)
    from games.models import GameTaskGroup
    if hasattr(published, 'filter'):
        return GameTaskGroup.order_queryset_by_number(published, reverse=reverse)
    return GameTaskGroup.sorted_links(published, reverse=reverse)


def get_alphabetty_hub_context(game, *, published_numbers: set[str] | None = None, now=None):
    now = now or timezone.now()
    today_num = current_alphabetty_number(game, now)
    start = alphabetty_publish_start(game)

    cta_number = None
    is_today = False
    cta_label = ''
    status = 'coming_soon'

    if today_num is not None and published_numbers and str(today_num) in published_numbers:
        cta_number = str(today_num)
        is_today = True
        cta_label = 'Сегодняшняя алфавитка'
        status = 'today'
    elif published_numbers:
        published_ints = sorted(int(n) for n in published_numbers if str(n).isdigit())
        visible = [n for n in published_ints if is_alphabetty_number_published(game, n, now)]
        if visible:
            latest = visible[-1]
            cta_number = str(latest)
            if today_num is not None and latest == today_num:
                is_today = True
                cta_label = 'Сегодняшняя алфавитка'
                status = 'today'
            else:
                cta_label = 'Последняя алфавитка'
                status = 'latest'
    elif today_num is not None and start and now < alphabetty_publish_at(game, 1):
        status = 'coming_soon'

    from games.section_paths import section_hub_path, section_last_path
    play_url = section_last_path(ALPHABETTY_GAME_ID) if cta_number else None
    section_url = section_hub_path(ALPHABETTY_GAME_ID)

    today_label = None
    if today_num is not None:
        today_label = f'№{today_num}'

    return {
        'alphabetty_game': game,
        'alphabetty_cta_number': cta_number,
        'alphabetty_cta_label': cta_label,
        'alphabetty_is_today': is_today,
        'alphabetty_play_url': play_url,
        'alphabetty_section_url': section_url,
        'alphabetty_status': status,
        'alphabetty_today_label': today_label,
        'alphabetty_publish_start': start,
    }
