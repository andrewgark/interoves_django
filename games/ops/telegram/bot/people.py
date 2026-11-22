"""How admin Telegram messages name people, teams and moments in time.

One format everywhere: a registered user is their name linked to the
/support card, then their Telegram handle in brackets; a team links to its
/support card; an anonymous player has no page, so only a short key is shown.
Times are Moscow time, the project's TIME_ZONE.
"""

from __future__ import annotations

import html

from django.utils import timezone

from games.ops.telegram.links import team_support_url, user_support_url


def _escape(text) -> str:
    return html.escape(str(text if text is not None else ''), quote=False)


def person_name(user) -> str:
    profile = getattr(user, 'profile', None)
    name = ''
    if profile is not None:
        name = ' '.join(filter(None, (
            (getattr(profile, 'first_name', '') or '').strip(),
            (getattr(profile, 'last_name', '') or '').strip(),
        )))
    return name or user.get_full_name() or user.get_username() or 'user #{}'.format(user.pk)


def telegram_handle(user) -> str:
    """Verified Telegram username first, then the handle the player typed in."""
    profile = getattr(user, 'profile', None)
    if profile is None:
        return ''
    for value in (getattr(profile, 'telegram_username', ''), getattr(profile, 'telegram_handle', '')):
        value = (value or '').strip().lstrip('@')
        if value:
            return value
    return ''


def user_html(user) -> str:
    """«<a>Имя Фамилия</a> (@handle)»; '—' when there is no user."""
    if user is None:
        return '—'
    label = '<a href="{}">{}</a>'.format(_escape(user_support_url(user)), _escape(person_name(user)))
    handle = telegram_handle(user)
    if handle:
        label += ' (@{})'.format(_escape(handle))
    return label


def team_html(team) -> str:
    if team is None:
        return '—'
    name = getattr(team, 'visible_name', None) or getattr(team, 'name', None) or team.pk
    return 'команда <a href="{}">{}</a>'.format(_escape(team_support_url(team)), _escape(name))


def anon_html(anon_key) -> str:
    key = str(anon_key or '')[:8]
    return 'аноним <code>{}</code>'.format(_escape(key)) if key else 'аноним'


def actor_html(*, user=None, team=None, anon_key=None) -> str:
    if team is not None:
        return team_html(team)
    if user is not None:
        return user_html(user)
    if anon_key:
        return anon_html(anon_key)
    return 'неизвестно'


def moscow_time(value, fmt: str = '%d.%m.%Y %H:%M') -> str:
    if value is None:
        return '—'
    return timezone.localtime(value).strftime(fmt)
