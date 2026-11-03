"""Which channels a player wants, and when Telegram may disturb them."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from games.notifications.kinds import GROUPS
from games.notifications.models import (
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    NotificationPrefs,
    NotificationSetting,
)

DEFAULT_TIMEZONE = 'Europe/Moscow'


def channels(user_id: int, group_key: str) -> tuple[bool, bool]:
    """(site, telegram) for this group, mandatory channels forced on."""
    group = GROUPS[group_key]
    row = NotificationSetting.objects.filter(user_id=user_id, group=group_key).first()
    site = row.site if row is not None else group.site_default
    telegram = row.telegram if row is not None else group.telegram_default
    return (site or group.site_mandatory, telegram or group.telegram_mandatory)


def set_channels(user_id: int, group_key: str, *, site: bool, telegram: bool) -> None:
    group = GROUPS[group_key]
    NotificationSetting.objects.update_or_create(
        user_id=user_id, group=group_key,
        defaults={'site': site or group.site_mandatory, 'telegram': telegram or group.telegram_mandatory},
    )


def prefs_for(user_id: int) -> NotificationPrefs:
    """The stored row, or an unsaved one with defaults."""
    row = NotificationPrefs.objects.filter(user_id=user_id).first()
    return row if row is not None else NotificationPrefs(user_id=user_id)


def user_timezone(profile) -> ZoneInfo:
    # The profile form may store «Europe/Moscow (UTC+03:00)»; the name comes first.
    name = (getattr(profile, 'timezone', '') or '').split(' ')[0] or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _in_interval(clock, start, end) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def outside_quiet_hours(at: datetime, prefs: NotificationPrefs, tz: ZoneInfo) -> datetime:
    """``at`` if Telegram may write then, otherwise the end of the quiet interval."""
    if not prefs.quiet_hours_enabled:
        return at
    start = prefs.quiet_start or DEFAULT_QUIET_START
    end = prefs.quiet_end or DEFAULT_QUIET_END
    local = at.astimezone(tz)
    if not _in_interval(local.timetz().replace(tzinfo=None), start, end):
        return at
    wake = datetime.combine(local.date(), end, tzinfo=tz)
    if wake <= local:
        wake = datetime.combine(local.date() + timedelta(days=1), end, tzinfo=tz)
    return wake


def with_any_channel(group_key: str, user_ids) -> list[int]:
    """The subset of ``user_ids`` that gets this group on at least one channel."""
    from django.db.models import Q

    group = GROUPS[group_key]
    user_ids = list(dict.fromkeys(user_ids))
    if group.site_default or group.telegram_default or group.site_mandatory or group.telegram_mandatory:
        off = set(
            NotificationSetting.objects.filter(group=group_key, user_id__in=user_ids, site=False, telegram=False)
            .values_list('user_id', flat=True)
        )
        return [user_id for user_id in user_ids if user_id not in off]
    on = set(
        NotificationSetting.objects.filter(group=group_key, user_id__in=user_ids)
        .filter(Q(site=True) | Q(telegram=True))
        .values_list('user_id', flat=True)
    )
    return [user_id for user_id in user_ids if user_id in on]
