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
    site = row.site if row is not None else True
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
