"""Telegram reminders that today's daily edition is out.

These never reach the site inbox: on the site the player already sees the
new edition. A reminder fires once per edition at the player's chosen
time: the channel post time, right after release, or a custom time in
Profile.timezone. Quiet hours do not apply, since the player picked the
time. Several reminders due at once go out as one message.
"""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from games.daily.page.section import MOSCOW
from games.notifications.models import DailyReminder, NotificationPrefs
from games.notifications.prefs import user_timezone

logger = logging.getLogger('application')

# Editions open at 00:00 MSK; the channel teaser is rendered at 00:15.
RELEASE_CLOCK = time(0, 20)
# Games without a channel post get this «channel» time.
CHANNEL_FALLBACK_CLOCK = {'alphabetty': time(12, 30)}
DEFAULT_CHANNEL_CLOCK = time(12, 30)
# A reminder this late (worker downtime) is skipped, not sent.
STALE_AFTER = timedelta(minutes=30)
SWEEP_LIMIT = 200
PHOTO_FILE_ID_TTL = 2 * 24 * 3600

GAME_EMOJI = {'ladder': '🪜', 'salad': '🥗', 'censorly': '🤐', 'alphabetty': '🔤'}


def _definitions():
    from games.daily.registry import DAILY_GAME_REGISTRY

    return [definition for definition in DAILY_GAME_REGISTRY.all() if definition.game_id in GAME_EMOJI]


def _definition(game_id):
    from games.daily.registry import DAILY_GAME_REGISTRY

    return DAILY_GAME_REGISTRY.get(game_id)


def reminder_games() -> list[tuple[str, str]]:
    """(game_id, title) of games a player can be reminded about."""
    return [(definition.game_id, definition.title) for definition in _definitions()]


def channel_clock(game_id: str, day=None) -> time:
    """The channel post time (MSK) for this game."""
    definition = _definition(game_id)
    if definition is not None and definition.channel_post_key:
        from games.ops.telegram.channels.daily_post import load_channel_post

        day = day or timezone.now().astimezone(MOSCOW).date()
        return load_channel_post(definition.channel_post_key).publish_at(day).astimezone(MOSCOW).time()
    return CHANNEL_FALLBACK_CLOCK.get(game_id, DEFAULT_CHANNEL_CLOCK)


def _clock(reminder: DailyReminder, profile):
    if reminder.mode == DailyReminder.MODE_CUSTOM and reminder.custom_time is not None:
        return reminder.custom_time, user_timezone(profile)
    if reminder.mode == DailyReminder.MODE_RELEASE:
        return RELEASE_CLOCK, MOSCOW
    return channel_clock(reminder.game_id), MOSCOW


def next_send_after(reminder: DailyReminder, profile, after: datetime) -> datetime:
    clock, tz = _clock(reminder, profile)
    local = after.astimezone(tz)
    candidate = datetime.combine(local.date(), clock, tzinfo=tz)
    if candidate <= after:
        candidate = datetime.combine(local.date() + timedelta(days=1), clock, tzinfo=tz)
    return candidate


# ---- settings --------------------------------------------------------------


def set_reminder(user, game_id: str, mode: str | None, *, custom_time=None, skip_if_played=True, now=None):
    """Turn a reminder on (``mode``) or off (``mode=None``)."""
    if _definition(game_id) is None or game_id not in GAME_EMOJI:
        raise ValueError('unknown daily game: {}'.format(game_id))
    if mode is None:
        DailyReminder.objects.filter(user=user, game_id=game_id).delete()
        return None
    if mode not in dict(DailyReminder.MODES):
        raise ValueError('unknown reminder mode: {}'.format(mode))
    if mode == DailyReminder.MODE_CUSTOM and custom_time is None:
        raise ValueError('custom mode needs a time')
    now = now or timezone.now()
    reminder = DailyReminder.objects.filter(user=user, game_id=game_id).first() or DailyReminder(
        user=user, game_id=game_id,
    )
    reminder.mode = mode
    reminder.custom_time = custom_time if mode == DailyReminder.MODE_CUSTOM else None
    reminder.skip_if_played = bool(skip_if_played)
    reminder.next_send_at = next_send_after(reminder, getattr(user, 'profile', None), now)
    reminder.save()
    return reminder


def reschedule(user, *, now=None) -> int:
    """Recompute send times after the player changed their time zone."""
    now = now or timezone.now()
    profile = getattr(user, 'profile', None)
    count = 0
    for reminder in DailyReminder.objects.filter(user=user):
        reminder.next_send_at = next_send_after(reminder, profile, now)
        reminder.save(update_fields=['next_send_at'])
        count += 1
    return count


# ---- editions --------------------------------------------------------------


@dataclass(frozen=True)
class Edition:
    game_id: str
    title: str
    number: int
    task_group_id: int
    play_url: str
    day: object  # MSK date of the edition


def current_edition(game_id: str, now: datetime) -> Edition | None:
    from games.models import Game, GameTaskGroup
    from games.ops.telegram.links import site_base_url
    from games.task_group.navigation import play_url_for_task_group

    definition = _definition(game_id)
    game = Game.objects.filter(id=game_id).first()
    if definition is None or game is None:
        return None
    schedule = definition.schedule
    number = schedule.current_number(game, now)
    if number is None or not schedule.is_published(game, number, now):
        return None
    link = GameTaskGroup.objects.filter(game=game, number=str(number), is_deferred=False).first()
    if link is None or link.task_group_id is None:
        return None
    published = schedule.publish_at(game, number)
    return Edition(
        game_id=game_id,
        title=definition.title,
        number=number,
        task_group_id=link.task_group_id,
        play_url=site_base_url() + play_url_for_task_group(game, number),
        day=(published or now).astimezone(MOSCOW).date(),
    )


def _played(user_id: int, edition: Edition) -> bool:
    from games.models import PlayerCompletedGame, PlayerStartedGame

    for model in (PlayerStartedGame, PlayerCompletedGame):
        if model.objects.filter(user_id=user_id, game_id=edition.game_id, task_group_id=edition.task_group_id).exists():
            return True
    return False


def _channel_post(edition: Edition):
    """Today's channel post (caption and teaser image), if the game has one."""
    from games.ops.social.models import SocialQueuePost
    from games.ops.telegram.channels.daily_post import load_channel_post

    definition = _definition(edition.game_id)
    if definition is None or not definition.channel_post_key:
        return None
    source = load_channel_post(definition.channel_post_key).source
    return (
        SocialQueuePost.objects.filter(source=source, ladder_date=edition.day)
        .exclude(image='').exclude(caption='')
        .order_by('-pk')
        .first()
    )


# ---- sending ---------------------------------------------------------------


def _settings_url() -> str:
    from games.ops.telegram.links import site_base_url

    return site_base_url() + reverse('new_notification_settings') + '#daily'


def _keyboard(editions: list[Edition]) -> dict:
    rows = [[{
        'text': 'Решить: {}'.format(edition.title) if len(editions) > 1 else 'Решить',
        'url': edition.play_url,
    }] for edition in editions]
    rows.append([{'text': 'Настроить напоминания', 'url': _settings_url()}])
    return {'inline_keyboard': rows}


def _line(edition: Edition) -> str:
    return '{} <b>{} №{}</b>'.format(
        GAME_EMOJI.get(edition.game_id, '🧩'), html.escape(edition.title, quote=False), edition.number,
    )


def _send(chat_id, editions: list[Edition]) -> str:
    from games.ops.telegram.transport.api import SEND_OK, send_message_status, send_photo_status

    keyboard = _keyboard(editions)
    if len(editions) == 1:
        post = _channel_post(editions[0])
        if post is not None:
            key = 'notifications:daily_photo:{}:{}'.format(post.pk, post.image.name)
            photo = cache.get(key)
            if not photo:
                with post.image.open('rb') as fh:
                    photo = fh.read()
            status, file_id = send_photo_status(chat_id, photo, caption=post.caption, reply_markup=keyboard)
            if status == SEND_OK and file_id:
                cache.set(key, file_id, PHOTO_FILE_ID_TTL)
            return status
        text = '{}\n\nВышла новая — не забудьте порешать.'.format(_line(editions[0]))
    else:
        text = '\n'.join(['Вышли новые ежедневки:', ''] + [_line(edition) for edition in editions])
    return send_message_status(chat_id, text, reply_markup=keyboard)


def _send_bundle(user, reminders: list[DailyReminder], now) -> dict:
    from games.ops.telegram.transport.api import SEND_BLOCKED, SEND_OK

    profile = getattr(user, 'profile', None)
    if not user.is_active or profile is None or not profile.telegram_verified or not profile.telegram_user_id:
        return {'sent': 0, 'skipped': len(reminders)}
    editions = []
    for reminder in reminders:
        edition = current_edition(reminder.game_id, now)
        if edition is None or reminder.last_sent_number == edition.number:
            continue
        if reminder.skip_if_played and _played(user.pk, edition):
            continue
        editions.append((reminder, edition))
    if not editions:
        return {'sent': 0, 'skipped': len(reminders)}
    status = _send(profile.telegram_user_id, [edition for _reminder, edition in editions])
    if status == SEND_BLOCKED:
        NotificationPrefs.objects.update_or_create(user_id=user.pk, defaults={'telegram_blocked_at': now})
    if status != SEND_OK:
        logger.info('event=daily_reminder_failed user=%s status=%s', user.pk, status)
        return {'sent': 0, 'skipped': len(reminders)}
    for reminder, edition in editions:
        DailyReminder.objects.filter(pk=reminder.pk).update(last_sent_number=edition.number)
    NotificationPrefs.objects.filter(user_id=user.pk, telegram_blocked_at__isnull=False).update(telegram_blocked_at=None)
    return {'sent': 1, 'skipped': len(reminders) - len(editions)}


def sweep(*, now=None) -> dict:
    """Send due reminders; called from the minute announcements tick."""
    now = now or timezone.now()
    due = list(
        DailyReminder.objects.filter(next_send_at__lte=now)
        .select_related('user', 'user__profile')
        .order_by('next_send_at')[:SWEEP_LIMIT]
    )
    by_user = defaultdict(list)
    stale = 0
    for reminder in due:
        profile = getattr(reminder.user, 'profile', None)
        upcoming = next_send_after(reminder, profile, now)
        # Claim by moving the time forward; a concurrent sweep loses the race.
        if not DailyReminder.objects.filter(pk=reminder.pk, next_send_at=reminder.next_send_at).update(
            next_send_at=upcoming,
        ):
            continue
        if now - reminder.next_send_at > STALE_AFTER:
            stale += 1
            continue
        by_user[reminder.user_id].append(reminder)
    sent = 0
    skipped = stale
    for reminders in by_user.values():
        try:
            result = _send_bundle(reminders[0].user, reminders, now)
        except Exception:
            logger.exception('event=daily_reminder_bundle_failed user=%s', reminders[0].user_id)
            skipped += len(reminders)
            continue
        sent += result['sent']
        skipped += result['skipped']
    return {'sent': sent, 'skipped': skipped}
