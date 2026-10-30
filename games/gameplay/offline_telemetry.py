"""What happens to offline play: counters, structured logs and throttled alerts.

docs/offline-play-plan.md, «Наблюдаемость». Pages report what went wrong in
the browser (``record_client_events``); the server counts its own events
(``count``). Every kind is counted per hour in the cache for the admin
report (``totals``); kinds that point at a bug also send a throttled admin
alert. Client reports are untrusted input: kinds and detail keys are
whitelisted, values are truncated, and alerts are capped per player and
globally. No answers or attempt texts are logged.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import timedelta

from django.core.cache import caches
from django.utils import timezone

from games.ops.analytics.identity import gameplay_anon_key
from games.site.http.auth_observability import log_auth_event

cache = caches['offline_play']

logger = logging.getLogger('games.offline')

# kind -> (label for people, alert admins?)
CLIENT_KINDS = {
    'queue_synced': ('Офлайн-ходов отправлено после восстановления связи', False),
    'pack_failed': ('Пакет офлайн-режима не загрузился', False),
    'storage_failed': ('Браузер не дал сохранить данные офлайн-режима', False),
    # status: 'register' (no worker) or 'cache' (the page was not kept).
    'sw_failed': ('Страница не сохранилась для открытия без сети', False),
    'queue_stale': ('Офлайн-ходы ждали отправки больше суток', False),
    'assemble_failed': ('Карточка не собралась из кусочков', True),
    'restore_failed': ('Прогресс не восстановился при открытии без сети', True),
    'queue_dropped': ('Офлайн-ход отклонён навсегда (другой аккаунт или игра)', True),
}
SERVER_KINDS = {
    'verdict_divergence': 'Вердикт страницы разошёлся с сервером',
    'pack_build_failed': 'Не собралась клиентская проверка задания',
    'offline_credit': 'Засчитано время офлайн-игры',
}
DETAIL_KEYS = ('task_id', 'status', 'count', 'age_h', 'word_index', 'error')
DETAIL_VALUE_MAX = 200
MAX_EVENTS_PER_REPORT = 20
MAX_COUNT_PER_EVENT = 1000
COUNTER_TTL_S = 9 * 24 * 60 * 60  # longer than the weekly report
PER_ACTOR_ALERT_WINDOW_S = 6 * 60 * 60
GLOBAL_ALERTS_PER_HOUR = 20


def _hour_key(kind, at):
    return 'offline_telemetry:{}:{}'.format(kind, at.astimezone(timezone.utc).strftime('%Y%m%d%H'))


def count(kind, n=1, *, now=None):
    """Add ``n`` to this hour's counter of ``kind``. Never raises."""
    key = _hour_key(kind, now or timezone.now())
    try:
        cache.add(key, 0, timeout=COUNTER_TTL_S)
        cache.incr(key, n)
    except Exception:
        logger.exception('event=offline_telemetry_count_failed kind=%s', kind)


def totals(since, until):
    """Counts per kind over the whole hours in [since, until)."""
    hours = []
    at = since.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while at < until:
        hours.append(at)
        at += timedelta(hours=1)
    result = {}
    for kind in list(CLIENT_KINDS) + list(SERVER_KINDS):
        keys = [_hour_key(kind, hour) for hour in hours]
        try:
            values = cache.get_many(keys)
        except Exception:
            logger.exception('event=offline_telemetry_totals_failed kind=%s', kind)
            values = {}
        total = sum(int(v or 0) for v in values.values())
        if total:
            result[kind] = total
    return result


def sanitize_detail(raw):
    if not isinstance(raw, dict):
        return {}
    detail = {}
    for key in DETAIL_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            detail[key] = value
        else:
            detail[key] = str(value)[:DETAIL_VALUE_MAX]
    return detail


def _actor_key(request):
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return 'user:{}'.format(user.pk)
    anon_key = str(gameplay_anon_key(request) or '')
    return 'anon:{}'.format(anon_key[:64]) if anon_key else 'unknown'


def _alert_allowed(actor_key, kind):
    try:
        if not cache.add('offline_telemetry:alert:{}:{}'.format(actor_key, kind), 1, PER_ACTOR_ALERT_WINDOW_S):
            return False
        global_key = 'offline_telemetry:alerts'
        cache.add(global_key, 0, timeout=60 * 60)
        return int(cache.incr(global_key)) <= GLOBAL_ALERTS_PER_HOUR
    except Exception:
        logger.exception('event=offline_telemetry_throttle_failed kind=%s', kind)
        return False


def _alert(kind, actor_key, detail, path):
    from games.ops.telegram.bot.alerts import publish_admin_alert

    text = '\n'.join([
        '📴 <b>Офлайн-режим: {}</b>'.format(html.escape(CLIENT_KINDS[kind][0])),
        '',
        'Игрок: <code>{}</code>'.format(html.escape(actor_key[:40])),
        'Страница: <code>{}</code>'.format(html.escape(path[:200])),
        'Подробности: <code>{}</code>'.format(html.escape(
            json.dumps(detail, ensure_ascii=False, sort_keys=True)[:800]
        )),
        'Повторы от этого игрока подавлены на 6 часов.',
    ])
    publish_admin_alert(
        alert='offline_play',
        dedupe_key='telegram.admin_alert:offline_play:client:{}:{}'.format(kind, actor_key),
        payload={'text': text},
    )


def record_client_events(request, events):
    """Count, log and, for bug kinds, alert the events a page reported.

    Returns the number of events accepted; unknown kinds are dropped.
    """
    if not isinstance(events, list):
        return 0
    actor_key = _actor_key(request)
    path = (request.META.get('HTTP_REFERER') or '')[:200]
    accepted = 0
    for event in events[:MAX_EVENTS_PER_REPORT]:
        if not isinstance(event, dict) or event.get('kind') not in CLIENT_KINDS:
            continue
        kind = event['kind']
        detail = sanitize_detail(event.get('detail'))
        try:
            n = max(1, min(int(detail.get('count') or 1), MAX_COUNT_PER_EVENT))
        except (TypeError, ValueError):
            n = 1
        count(kind, n)
        log_auth_event('offline_client_event', request, kind=kind, actor=actor_key, details=detail)
        if CLIENT_KINDS[kind][1] and _alert_allowed(actor_key, kind):
            try:
                _alert(kind, actor_key, detail, path)
            except Exception:
                logger.exception('event=offline_telemetry_alert_failed kind=%s', kind)
        accepted += 1
    return accepted


def report_lines(since, until):
    """Lines for the admin report, or [] when nothing happened."""
    counts = totals(since, until)
    if not counts:
        return []
    lines = ['<b>Офлайн-режим</b>']
    for kind, label in [(k, v[0]) for k, v in CLIENT_KINDS.items()] + list(SERVER_KINDS.items()):
        if counts.get(kind):
            lines.append('{}: {}'.format(label, counts[kind]))
    return lines
