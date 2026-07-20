from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from games.models import Game
from games.telegram.announcements import ANNOUNCEMENT_FORMATTERS
from games.telegram.config import game_telegram_announce_enabled
from games.telegram.models import TelegramGameAnnouncement
from games.telegram.notify import send_admin_message, send_announce_message


def _should_consider_game(game: Game, now) -> bool:
    if not game_telegram_announce_enabled(game):
        return False
    start = game.get_visible_start_time()
    end = game.get_visible_end_time()
    if start is None or end is None:
        return False
    window_start = start - timedelta(hours=1)
    window_end = end + timedelta(hours=1)
    return window_start <= now <= window_end


def _mark_and_send(game: Game, kind: str) -> bool:
    formatter = ANNOUNCEMENT_FORMATTERS.get(kind)
    if formatter is None:
        return False
    with transaction.atomic():
        _, created = TelegramGameAnnouncement.objects.get_or_create(game=game, kind=kind)
        if not created:
            return False
    text = formatter(game)
    send_announce_message(text)
    return True


def process_game_announcements(now=None) -> dict[str, int]:
    now = now or timezone.now()
    stats = {'start': 0, 'end_soon_30': 0, 'end': 0, 'admin_start_soon': 0}

    for game in Game.objects.all():
        start = game.get_visible_start_time()
        end = game.get_visible_end_time()
        if start is None or end is None:
            continue

        if game_telegram_announce_enabled(game) and _should_consider_game(game, now):
            if now >= start and _mark_and_send(game, TelegramGameAnnouncement.KIND_START):
                stats['start'] += 1

            if now >= end - timedelta(minutes=30) and now < end:
                if _mark_and_send(game, TelegramGameAnnouncement.KIND_END_SOON_30):
                    stats['end_soon_30'] += 1

            if now >= end and _mark_and_send(game, TelegramGameAnnouncement.KIND_END):
                stats['end'] += 1

        if _should_admin_start_soon(game, now):
            if _mark_admin_start_soon(game):
                stats['admin_start_soon'] += 1
                notify_admin_game_lifecycle(game, 'start_soon')

    try:
        from games.telegram.ladder_channel import process_ladder_channel_tick
        ladder_stats = process_ladder_channel_tick(now=now)
        stats['ladder_scheduled'] = ladder_stats.get('scheduled', 0)
    except Exception:
        # Never break game announcements because of the ladder channel job.
        import logging
        logging.getLogger('application').exception('Ladder channel tick failed')
        stats['ladder_scheduled'] = 0

    return stats
def _should_admin_start_soon(game: Game, now) -> bool:
    start = game.get_visible_start_time()
    if start is None:
        return False
    window_start = start - timedelta(hours=1, minutes=5)
    window_end = start - timedelta(minutes=55)
    return window_start <= now <= window_end


def _mark_admin_start_soon(game: Game) -> bool:
    from django.core.cache import cache

    key = 'telegram:admin:start_soon:{}'.format(game.id)
    if cache.get(key):
        return False
    cache.set(key, True, timeout=7 * 24 * 3600)
    return True


def notify_admin_game_lifecycle(game, event: str) -> None:
    from games.telegram.notify import format_admin_game_lifecycle_message

    send_admin_message(format_admin_game_lifecycle_message(game, event))


def notify_admin_registration_milestone(game, count: int) -> None:
    from games.telegram.notify import format_admin_registration_milestone_message

    send_admin_message(format_admin_registration_milestone_message(game, count))
