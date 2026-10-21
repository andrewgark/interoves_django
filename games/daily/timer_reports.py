"""Client reports about a daily solve timer that could not run.

The browser reports when its timer failed to start (after its retry budget),
when the server accepted a start that did not leave the clock running, or
when a visible page still had no running clock after a while. Each report is
logged as a structured event, and a throttled Telegram alert tells the admins
so they can look into the cause.
"""

from __future__ import annotations

import html
import json
import logging

from django.core.cache import cache

from games.analytics_identity import gameplay_anon_key
from games.auth_observability import log_auth_event, normalized_user_agent

logger = logging.getLogger('games.daily_timing')

CLIENT_REPORT_ACTION = 'client_report'
CLIENT_REPORT_REASONS = {
    'start_failed': 'Таймер не запустился после всех попыток',
    'start_not_running': 'Сервер принял старт, но таймер не идёт',
    'never_started': 'Видимая страница без идущего таймера',
    'stopped_while_active': 'Таймер остановился, а игрок продолжает решать',
}
# Only these diagnostics are kept; anything else from the client is dropped.
DETAIL_KEYS = (
    'online', 'visibility', 'status', 'authoritative', 'start_attempts',
    'start_failed', 'delivery_failed', 'awaiting_server', 'resume_required',
    'manually_paused', 'idle_paused', 'queue_length', 'queue_stalled',
    'last_action', 'last_status', 'last_error', 'last_network',
    'session_id', 'capability', 'page_age_s', 'response_status',
)
DETAIL_VALUE_MAX = 120
PER_ACTOR_WINDOW_S = 6 * 60 * 60
GLOBAL_ALERTS_PER_HOUR = 30


def sanitize_details(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    details = {}
    for key in DETAIL_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            details[key] = value
        else:
            details[key] = str(value)[:DETAIL_VALUE_MAX]
    return details


def _actor_label(request) -> tuple[str, str]:
    """Return (throttle key, human label) for the reporting player."""
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        profile = getattr(user, 'profile', None)
        name = ' '.join(filter(None, (
            getattr(profile, 'first_name', ''), getattr(profile, 'last_name', ''),
        ))) if profile else ''
        label = '{} (user #{})'.format(name or user.get_username(), user.pk)
        return 'user:{}'.format(user.pk), label
    anon_key = str(gameplay_anon_key(request) or '')
    if anon_key:
        return 'anon:{}'.format(anon_key[:64]), 'anon {}'.format(anon_key[:12])
    return 'unknown', 'unknown player'


def _alert_allowed(actor_key: str, path: str, reason: str) -> bool:
    """One alert per player, page and reason per window; a global hourly cap."""
    try:
        if not cache.add(
            'daily_timer_report:{}:{}:{}'.format(actor_key, path, reason),
            1,
            timeout=PER_ACTOR_WINDOW_S,
        ):
            return False
        global_key = 'daily_timer_report:global'
        cache.add(global_key, 0, timeout=60 * 60)
        return int(cache.incr(global_key)) <= GLOBAL_ALERTS_PER_HOUR
    except Exception:
        logger.exception('event=daily_timer_report_throttle_failed')
        return False


def _alert_text(*, reason, actor_label, path, details, user_agent) -> str:
    browser = '{} {} · {}'.format(
        user_agent.get('browser_family') or '?',
        user_agent.get('browser_major') or '',
        user_agent.get('os_family') or '?',
    )
    return '\n'.join([
        '⏱ <b>Таймер решения не идёт</b>',
        '',
        'Причина: {}'.format(html.escape(CLIENT_REPORT_REASONS[reason])),
        'Игрок: <b>{}</b>'.format(html.escape(actor_label)),
        'Страница: <code>{}</code>'.format(html.escape(path)),
        'Браузер: {}'.format(html.escape(browser)),
        'Диагностика: <code>{}</code>'.format(html.escape(
            json.dumps(details, ensure_ascii=False, sort_keys=True)[:1500]
        )),
    ])


def handle_client_report(request, payload: dict) -> tuple[bool, str]:
    """Log and alert one client report. Returns (accepted, error code)."""
    reason = payload.get('reason')
    if reason not in CLIENT_REPORT_REASONS:
        return False, 'bad_reason'
    details = sanitize_details(payload.get('details'))
    path = (getattr(request, 'path', '') or '')[:200]
    actor_key, actor_label = _actor_label(request)
    log_auth_event(
        'daily_timer_client_report',
        request,
        reason=reason,
        actor=actor_key,
        details=details,
    )
    if _alert_allowed(actor_key, path, reason):
        try:
            from games.telegram.admin_alert_events import publish_admin_alert

            publish_admin_alert(
                alert='daily_timer_client_report',
                dedupe_key='telegram.admin_alert:daily_timer_client_report:{}:{}:{}'.format(
                    actor_key, path, reason,
                ),
                payload={'text': _alert_text(
                    reason=reason,
                    actor_label=actor_label,
                    path=path,
                    details=details,
                    user_agent=normalized_user_agent(request.META.get('HTTP_USER_AGENT')),
                )},
            )
        except Exception:
            logger.exception('event=daily_timer_report_alert_failed reason=%s', reason)
    return True, ''
