"""Notification settings: channels per group, quiet hours, daily reminders."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from games.identity import has_profile
from games.notifications import daily
from games.notifications.kinds import GROUPS
from games.notifications.models import DailyReminder, NotificationPrefs
from games.notifications.prefs import channels, prefs_for, set_channels, user_timezone
from games.site.pages import NEW_UI_PROJECT, _project_urls_context


def _parse_time(value) -> time | None:
    try:
        return time.fromisoformat((value or '').strip()[:5])
    except ValueError:
        return None


def _clock_label(clock: time, tz: ZoneInfo, viewer_tz: ZoneInfo) -> str:
    """HH:MM of a clock in ``tz``, shown in the viewer's zone."""
    today = timezone.now().astimezone(tz).date()
    return datetime.combine(today, clock, tzinfo=tz).astimezone(viewer_tz).strftime('%H:%M')


def _group_rows(user_id):
    rows = []
    for group in GROUPS.values():
        site, telegram = channels(user_id, group.key)
        rows.append({'group': group, 'site': site, 'telegram': telegram})
    return rows


def _daily_rows(user, viewer_tz):
    from games.daily.page.section import MOSCOW

    reminders = {row.game_id: row for row in DailyReminder.objects.filter(user=user)}
    rows = []
    for game_id, title in daily.reminder_games():
        reminder = reminders.get(game_id)
        rows.append({
            'game_id': game_id,
            'title': title,
            'mode': reminder.mode if reminder else 'off',
            'custom_time': reminder.custom_time.strftime('%H:%M') if reminder and reminder.custom_time else '',
            'skip_if_played': reminder.skip_if_played if reminder else True,
            'channel_label': _clock_label(daily.channel_clock(game_id), MOSCOW, viewer_tz),
            'release_label': _clock_label(daily.RELEASE_CLOCK, MOSCOW, viewer_tz),
        })
    return rows


def _telegram_linked(user) -> bool:
    profile = getattr(user, 'profile', None)
    return bool(profile is not None and profile.telegram_verified and profile.telegram_user_id)


def _save_channels(request, *, telegram_linked):
    for group in GROUPS.values():
        # Disabled inputs are not submitted: without Telegram keep what was set.
        telegram = (
            request.POST.get('tg_{}'.format(group.key)) == '1'
            if telegram_linked else channels(request.user.pk, group.key)[1]
        )
        set_channels(
            request.user.pk, group.key,
            site=request.POST.get('site_{}'.format(group.key)) == '1',
            telegram=telegram,
        )
    prefs = prefs_for(request.user.pk)
    prefs.quiet_hours_enabled = request.POST.get('quiet_enabled') == '1'
    start = _parse_time(request.POST.get('quiet_start'))
    end = _parse_time(request.POST.get('quiet_end'))
    if start is not None:
        prefs.quiet_start = start
    if end is not None:
        prefs.quiet_end = end
    prefs.save()


def _save_daily(request):
    errors = []
    for game_id, title in daily.reminder_games():
        mode = request.POST.get('daily_{}'.format(game_id)) or 'off'
        custom_time = _parse_time(request.POST.get('daily_time_{}'.format(game_id)))
        skip = request.POST.get('daily_skip_{}'.format(game_id)) == '1'
        if mode == DailyReminder.MODE_CUSTOM and custom_time is None:
            errors.append('{}: укажите время напоминания.'.format(title))
            continue
        if mode not in dict(DailyReminder.MODES):
            mode = None
        daily.set_reminder(request.user, game_id, mode, custom_time=custom_time, skip_if_played=skip)
    return errors


def _save_timezone(request):
    name = (request.POST.get('timezone') or '').strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise Http404()
    profile = request.user.profile
    profile.timezone = name
    profile.save(update_fields=['timezone'])
    daily.reschedule(request.user)


@login_required
@require_http_methods(['GET', 'POST'])
def notification_settings(request):
    if not has_profile(request.user):
        return redirect('new_profile')
    if request.method == 'POST':
        section = request.POST.get('section')
        if section == 'timezone':
            _save_timezone(request)
            messages.success(request, 'Часовой пояс обновлён.')
            return redirect('new_notification_settings')
        if section != 'all':
            raise Http404()
        telegram_linked = _telegram_linked(request.user)
        _save_channels(request, telegram_linked=telegram_linked)
        errors = _save_daily(request) if telegram_linked else []
        for error in errors:
            messages.error(request, error)
        if not errors:
            messages.success(request, 'Настройки уведомлений сохранены.')
        return redirect('new_notification_settings')

    profile = request.user.profile
    viewer_tz = user_timezone(profile)
    prefs = NotificationPrefs.objects.filter(user=request.user).first() or NotificationPrefs(user=request.user)
    telegram_linked = _telegram_linked(request.user)
    ctx = {
        'page_title': 'Уведомления',
        'group_rows': _group_rows(request.user.pk),
        'daily_rows': _daily_rows(request.user, viewer_tz),
        'prefs': prefs,
        'quiet_start': prefs.quiet_start.strftime('%H:%M'),
        'quiet_end': prefs.quiet_end.strftime('%H:%M'),
        'profile_timezone': viewer_tz.key,
        'telegram_linked': telegram_linked,
        'telegram_blocked': telegram_linked and prefs.telegram_blocked_at is not None,
        'telegram_bot_username': settings.TELEGRAM_BOT_USERNAME,
    }
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/notification_settings.html', ctx)


@login_required
@require_http_methods(['POST'])
def daily_reminder_enable(request):
    """One-tap opt-in from the prompt after a solved daily game."""
    from django.http import JsonResponse

    game_id = (request.POST.get('game_id') or '').strip()
    if game_id not in dict(daily.reminder_games()) or not _telegram_linked(request.user):
        raise Http404()
    daily.set_reminder(request.user, game_id, DailyReminder.MODE_CHANNEL)
    return JsonResponse({'ok': True})
