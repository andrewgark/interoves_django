"""Minute cron: chat game lifecycle announcements + admin start-soon + ladder channel."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from games.models import Attempt, Game, Team
from games.telegram.announcements import (
    ANNOUNCEMENT_FORMATTERS,
    build_podium,
    format_all_solved_announcement,
    format_game_results_announcement,
)
from games.telegram.config import game_telegram_announce_enabled
from games.telegram.models import TelegramGameAnnouncement
from games.telegram.notify import (
    send_admin_message,
    send_announce_message,
    send_announce_photo,
)

logger = logging.getLogger('application')


def _should_consider_game(game: Game, now) -> bool:
    if not game_telegram_announce_enabled(game):
        return False
    start = game.start_time
    end = game.end_time
    if start is None or end is None:
        return False
    window_start = start - timedelta(hours=25)
    window_end = end + timedelta(minutes=20)
    return window_start <= now <= window_end


def _try_mark(game: Game, kind: str) -> bool:
    """Claim the announcement slot. Returns True if this caller should send."""
    with transaction.atomic():
        _, created = TelegramGameAnnouncement.objects.get_or_create(game=game, kind=kind)
        return created


def _mark_and_send_text(game: Game, kind: str) -> bool:
    formatter = ANNOUNCEMENT_FORMATTERS.get(kind)
    if formatter is None:
        return False
    if not _try_mark(game, kind):
        return False
    send_announce_message(formatter(game))
    return True


def _mark_and_send_photo(game: Game, kind: str, caption: str, photo_bytes: bytes | None) -> bool:
    if not _try_mark(game, kind):
        return False
    if photo_bytes and send_announce_photo(photo_bytes, caption=caption, filename='results.png'):
        return True
    send_announce_message(caption)
    return True


def _tournament_results_png(game: Game) -> bytes | None:
    try:
        from games.telegram.results_image import render_tournament_results_png

        return render_tournament_results_png(game)
    except Exception:
        logger.exception('Tournament results screenshot failed for game %s', game.id)
        return None


def _teams_with_all_tasks_ok(game: Game) -> list[Team]:
    from games.views.new_ui import _load_results_placements_and_tasks

    _placements, _tg_map, tasks_flat, task_ids, _headers = _load_results_placements_and_tasks(game)
    if not task_ids:
        return []

    bulk = Attempt.manager.get_bulk_game_actor_rows(task_ids, mode='tournament', game=game)
    ok_counts: dict[int, int] = {}
    teams_by_id: dict[int, Team] = {}
    for _task_id, rows in bulk.items():
        for participant, info in rows:
            if not isinstance(participant, Team):
                continue
            best = info.best_attempt
            if best is None or best.status != 'Ok':
                continue
            ok_counts[participant.pk] = ok_counts.get(participant.pk, 0) + 1
            teams_by_id[participant.pk] = participant

    n_tasks = len(task_ids)
    winners = [
        teams_by_id[tid]
        for tid, count in ok_counts.items()
        if count >= n_tasks
    ]
    winners.sort(key=lambda t: ((t.visible_name or t.name or '').lower(), t.pk))
    return winners


def _process_all_solved(game: Game, now, stats: dict) -> None:
    if now < game.start_time or now > game.end_time:
        return
    png = None
    for team in _teams_with_all_tasks_ok(game):
        kind = TelegramGameAnnouncement.all_solved_kind(team.pk)
        if TelegramGameAnnouncement.objects.filter(game=game, kind=kind).exists():
            continue
        caption = format_all_solved_announcement(game, team)
        if png is None:
            png = _tournament_results_png(game)
        if _mark_and_send_photo(game, kind, caption, png):
            stats['all_solved'] += 1


def _game_has_pending_attempts(game: Game) -> bool:
    return Attempt.manager.filter(game=game, status='Pending', skip=False).exists()


def _process_results(game: Game, now, stats: dict) -> None:
    end = game.end_time
    if now < end or now > end + timedelta(minutes=15):
        return
    if TelegramGameAnnouncement.objects.filter(
        game=game, kind=TelegramGameAnnouncement.KIND_RESULTS,
    ).exists():
        return
    if _game_has_pending_attempts(game):
        return

    from games.views.new_ui import _load_game_results_data

    data = _load_game_results_data(game, 'tournament')
    podium = build_podium(data.get('team_to_place') or {})
    caption = format_game_results_announcement(game, podium)
    png = _tournament_results_png(game)
    if _mark_and_send_photo(game, TelegramGameAnnouncement.KIND_RESULTS, caption, png):
        stats['results'] += 1


def process_game_announcements(now=None) -> dict[str, int]:
    now = now or timezone.now()
    stats = {
        'day_before': 0,
        'hour_before': 0,
        'start': 0,
        'end_soon_15': 0,
        'end': 0,
        'all_solved': 0,
        'results': 0,
        'admin_start_soon': 0,
    }

    for game in Game.objects.all():
        start = game.start_time
        end = game.end_time
        if start is None or end is None:
            continue

        if game_telegram_announce_enabled(game) and _should_consider_game(game, now):
            if now >= start - timedelta(days=1) and now < start:
                if _mark_and_send_text(game, TelegramGameAnnouncement.KIND_DAY_BEFORE):
                    stats['day_before'] += 1

            if now >= start - timedelta(hours=1) and now < start:
                if _mark_and_send_text(game, TelegramGameAnnouncement.KIND_HOUR_BEFORE):
                    stats['hour_before'] += 1

            if now >= start:
                if _mark_and_send_text(game, TelegramGameAnnouncement.KIND_START):
                    stats['start'] += 1

            if now >= end - timedelta(minutes=15) and now < end:
                if _mark_and_send_text(game, TelegramGameAnnouncement.KIND_END_SOON_15):
                    stats['end_soon_15'] += 1

            if now >= end:
                if _mark_and_send_text(game, TelegramGameAnnouncement.KIND_END):
                    stats['end'] += 1

            try:
                _process_all_solved(game, now, stats)
            except Exception:
                logger.exception('all_solved announce failed for game %s', game.id)

            try:
                _process_results(game, now, stats)
            except Exception:
                logger.exception('results announce failed for game %s', game.id)

        if _should_admin_start_soon(game, now):
            if _mark_admin_start_soon(game):
                stats['admin_start_soon'] += 1
                notify_admin_game_lifecycle(game, 'start_soon')

    try:
        from games.telegram.ladder_channel import process_ladder_channel_tick

        ladder_stats = process_ladder_channel_tick(now=now)
        stats['ladder_scheduled'] = ladder_stats.get('scheduled', 0)
    except Exception:
        logger.exception('Ladder channel tick failed')
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
