"""Minute cron: chat game lifecycle announcements + admin start-soon + ladder channel."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from games.models import Attempt, Game, Team
from games.social.models import SocialQueuePost
from games.social.publish import (
    publish_instagram,
    publish_telegram,
    publish_twitter,
    queue_failed_network_retries,
)
from games.telegram.announcements import (
    ANNOUNCEMENT_FORMATTERS,
    build_podium,
    format_all_solved_announcement,
    format_game_day_before_announcement,
    format_game_results_announcement,
    format_no_coffins_announcement,
)
from games.telegram.config import game_telegram_announce_enabled
from games.telegram.game_urls import game_site_url
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


def _unmark(game: Game, kind: str) -> None:
    """Release a failed delivery so the next cron tick can retry it."""
    TelegramGameAnnouncement.objects.filter(game=game, kind=kind).delete()


def _mark_and_send_text(game: Game, kind: str) -> bool:
    formatter = ANNOUNCEMENT_FORMATTERS.get(kind)
    if formatter is None:
        return False
    if not _try_mark(game, kind):
        return False
    if send_announce_message(formatter(game)):
        return True
    _unmark(game, kind)
    return False


def _mark_and_send_photo(game: Game, kind: str, caption: str, photo_bytes: bytes | None) -> bool:
    if not _try_mark(game, kind):
        return False
    if photo_bytes and send_announce_photo(photo_bytes, caption=caption, filename='results.png'):
        return True
    if send_announce_message(caption):
        return True
    _unmark(game, kind)
    return False


def _publish_social_now(
    game: Game,
    caption: str,
    photo_bytes: bytes,
    *,
    filename: str,
    social_photo_bytes: bytes | None = None,
) -> SocialQueuePost:
    """Create a SocialQueuePost and publish immediately to TG channel + X + Instagram."""
    post = SocialQueuePost.objects.create(
        source=SocialQueuePost.SOURCE_GAME,
        caption=caption or '',
        play_url=game_site_url(game),
    )
    post.set_image_bytes(photo_bytes, filename=filename)
    if social_photo_bytes:
        post.set_social_image_bytes(
            social_photo_bytes,
            filename='social-{}'.format(filename),
        )
    post.save()
    publish_telegram(post, immediate=True, force=False)
    post.refresh_from_db()
    publish_twitter(post, force=False)
    post.refresh_from_db()
    publish_instagram(post, force=False)
    post.refresh_from_db()
    queue_failed_network_retries(post)
    return post


def _mark_and_publish_social(
    game: Game,
    kind: str,
    caption: str,
    photo_bytes: bytes | None,
    *,
    filename: str,
    social_photo_bytes: bytes | None = None,
) -> bool:
    """
    Publish to all three social networks immediately.

    Image first: if missing, do not claim the announcement slot (retry next minute).
    """
    if not photo_bytes:
        return False
    if not _try_mark(game, kind):
        return False
    try:
        _publish_social_now(
            game,
            caption,
            photo_bytes,
            filename=filename,
            social_photo_bytes=social_photo_bytes,
        )
    except Exception:
        logger.exception(
            'Social publish failed for game %s kind=%s', game.id, kind,
        )
        _unmark(game, kind)
        return False
    return True


def _tournament_results_png(game: Game) -> bytes | None:
    try:
        from games.telegram.results_image import render_tournament_results_png

        return render_tournament_results_png(game)
    except Exception:
        logger.exception('Tournament results screenshot failed for game %s', game.id)
        return None


def _tournament_results_social_png(game: Game) -> bytes | None:
    try:
        from games.telegram.results_image import render_tournament_results_social_png

        return render_tournament_results_social_png(game)
    except Exception:
        logger.exception('Compact results screenshot failed for game %s', game.id)
        return None


def _fresh_tournament_results_png(game: Game, cache: dict) -> bytes | None:
    """Render at most once per game/tick, after invalidating the live table cache."""
    key = str(game.pk)
    if key not in cache:
        from games.results_snapshot import invalidate_live_results_cache

        invalidate_live_results_cache(game, mode='tournament')
        cache[key] = _tournament_results_png(game)
    return cache[key]


def _game_announce_png(game: Game) -> bytes | None:
    try:
        from games.telegram.announce_image import render_game_announce_png

        return render_game_announce_png(game)
    except Exception:
        logger.exception('Game announce screenshot failed for game %s', game.id)
        return None


def _process_day_before(game: Game, now, stats: dict) -> None:
    start = game.start_time
    if now < start - timedelta(days=1) or now >= start:
        return
    if TelegramGameAnnouncement.objects.filter(
        game=game, kind=TelegramGameAnnouncement.KIND_DAY_BEFORE,
    ).exists():
        return
    caption = format_game_day_before_announcement(game)
    png = _game_announce_png(game)
    if _mark_and_publish_social(
        game,
        TelegramGameAnnouncement.KIND_DAY_BEFORE,
        caption,
        png,
        filename='announce-{}.png'.format(game.id),
    ):
        stats['day_before'] += 1


def _scoring_hint_count(attempts_info) -> int:
    """Count real player hints (same exclusions as AttemptsInfo.get_sum_hint_penalty)."""
    from games.raddle import is_raddle_in_game_assist_hint

    n = 0
    for hint_attempt in attempts_info.hint_attempts or []:
        if not hint_attempt.is_real_request:
            continue
        hint = hint_attempt.hint
        if hint is None or is_raddle_in_game_assist_hint(hint):
            continue
        n += 1
    return n


def _teams_with_all_tasks_ok(game: Game) -> list[tuple[Team, int, object]]:
    """Teams with Ok on every visible game task, plus (hint_count, hint_penalty)."""
    from decimal import Decimal

    from django.db.models import Case, F, IntegerField, Value, When, Window
    from django.db.models.functions import RowNumber

    from games.models import Attempt, HintAttempt, Team
    from games.raddle import is_raddle_in_game_assist_hint
    from games.views.new_ui import _load_results_placements_and_tasks

    _placements, _tg_map, _tasks_flat, task_ids, _headers = _load_results_placements_and_tasks(game)
    if not task_ids:
        return []

    # Tournament window = attempt_is_tournament (start_time <= time <= end_time).
    att_base = Attempt.manager.filter(
        task_id__in=task_ids,
        skip=False,
        game=game,
        team_id__isnull=False,
        user__isnull=True,
        anon_key__isnull=True,
    )
    if game.start_time is not None:
        att_base = att_base.filter(time__gte=game.start_time)
    if game.end_time is not None:
        att_base = att_base.filter(time__lte=game.end_time)

    status_rank = Case(
        When(status='Ok', then=Value(3)),
        When(status='Partial', then=Value(2)),
        When(status='Pending', then=Value(1)),
        When(status='Wrong', then=Value(0)),
        default=Value(-1),
        output_field=IntegerField(),
    )
    best_rows = (
        att_base.annotate(
            status_rank=status_rank,
            rn=Window(
                expression=RowNumber(),
                partition_by=[F('task_id'), F('team_id')],
                order_by=[
                    F('points').desc(),
                    F('status_rank').desc(),
                    F('time').asc(),
                ],
            ),
        )
        .filter(rn=1)
        .values('task_id', 'team_id', 'status', 'time')
    )

    ok_counts: dict = {}
    completion_times: dict = {}
    for row in best_rows:
        tid = row['team_id']
        if row['status'] == 'Ok':
            ok_counts[tid] = ok_counts.get(tid, 0) + 1
            completed_at = row['time']
            previous = completion_times.get(tid)
            if completed_at is not None and (previous is None or completed_at > previous):
                completion_times[tid] = completed_at

    # Hints: same tournament window on HintAttempt.time
    hint_qs = HintAttempt.objects.filter(
        hint__task_id__in=task_ids,
        is_real_request=True,
        team_id__isnull=False,
        user__isnull=True,
        anon_key__isnull=True,
    ).select_related('hint')
    if game.start_time is not None:
        hint_qs = hint_qs.filter(time__gte=game.start_time)
    if game.end_time is not None:
        hint_qs = hint_qs.filter(time__lte=game.end_time)

    hint_counts: dict = {}
    hint_penalties: dict = {}
    for ha in hint_qs:
        tid = ha.team_id
        if not tid:
            continue
        hint = ha.hint
        if hint is None or is_raddle_in_game_assist_hint(hint):
            continue
        hint_counts[tid] = hint_counts.get(tid, 0) + 1
        hint_penalties[tid] = hint_penalties.get(tid, Decimal(0)) + Decimal(
            str(hint.points_penalty or 0)
        )

    n_tasks = len(task_ids)
    winner_ids = [tid for tid, count in ok_counts.items() if count >= n_tasks]
    if not winner_ids:
        return []

    teams_by_id = {t.pk: t for t in Team.objects.filter(pk__in=winner_ids, is_hidden=False)}
    winners = [
        (
            teams_by_id[tid],
            hint_counts.get(tid, 0),
            hint_penalties.get(tid, Decimal(0)),
            completion_times.get(tid),
        )
        for tid in winner_ids
        if tid in teams_by_id
    ]
    winners.sort(key=lambda row: (
        row[3] or timezone.now(),
        row[0].pk,
    ))
    return winners


def _process_all_solved(game: Game, now, stats: dict, results_png_cache: dict) -> None:
    if now < game.start_time or now > game.end_time:
        return
    png = None
    for team, hint_count, hint_penalty, _completed_at in _teams_with_all_tasks_ok(game):
        kind = TelegramGameAnnouncement.all_solved_kind(team.pk)
        if TelegramGameAnnouncement.objects.filter(game=game, kind=kind).exists():
            continue
        caption = format_all_solved_announcement(
            game, team, hint_count=hint_count, hint_penalty=hint_penalty,
        )
        if png is None:
            png = _fresh_tournament_results_png(game, results_png_cache)
        if _mark_and_send_photo(game, kind, caption, png):
            stats['all_solved'] += 1


def _last_task_first_taken_for_full_score(game: Game):
    """Return the task that most recently got its first full-score tournament result."""
    from decimal import Decimal, InvalidOperation

    from games.views.new_ui import _load_results_placements_and_tasks

    placements, task_group_to_tasks, tasks, task_ids, _headers = (
        _load_results_placements_and_tasks(game)
    )
    if not task_ids:
        return None

    placement_by_task_id = {}
    for placement in placements:
        placement_tasks = task_group_to_tasks[placement.number]
        for task in placement_tasks:
            display_number = placement.number
            if len(placement_tasks) > 1:
                display_number = '{}.{}'.format(placement.number, task.number)
            placement_by_task_id[task.id] = (
                display_number,
                placement.name,
                placement.key_sort(),
                task.key_sort(),
            )

    rows_by_task = Attempt.manager.get_bulk_game_actor_rows(
        task_ids, mode='tournament', game=game,
    )
    first_takes = []
    for task in tasks:
        try:
            max_points = Decimal(str(task.get_results_max_points()))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if max_points <= 0:
            return None

        full_score_rows = []
        for team, attempts_info in rows_by_task.get(task.id, []):
            if not isinstance(team, Team) or team.is_hidden:
                continue
            try:
                result_points = Decimal(str(attempts_info.get_result_points()))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if result_points < max_points:
                continue
            result_attempt = attempts_info.get_result_attempt()
            if result_attempt is None or result_attempt.time is None:
                continue
            full_score_rows.append((result_attempt.time, team.pk, team))

        if not full_score_rows:
            return None
        taken_at, _team_pk, team = min(full_score_rows, key=lambda row: (row[0], row[1]))
        display_number, task_name, placement_key, task_key = placement_by_task_id[task.id]
        first_takes.append((
            taken_at, placement_key, task_key, display_number, task_name, team,
        ))

    if not first_takes:
        return None
    taken_at, _placement_key, _task_key, task_number, task_name, team = max(
        first_takes, key=lambda row: (row[0], row[1], row[2]),
    )
    return task_number, task_name, team, taken_at


def _process_no_coffins(game: Game, now, stats: dict, results_png_cache: dict) -> None:
    if now < game.start_time or now > game.end_time:
        return
    kind = TelegramGameAnnouncement.KIND_NO_COFFINS
    if TelegramGameAnnouncement.objects.filter(game=game, kind=kind).exists():
        return

    last_take = _last_task_first_taken_for_full_score(game)
    if last_take is None:
        return
    caption = format_no_coffins_announcement(*last_take)
    png = _fresh_tournament_results_png(game, results_png_cache)
    if not png or not _try_mark(game, kind):
        return
    if send_announce_photo(png, caption=caption, filename='results.png'):
        stats['no_coffins'] += 1
        return
    # The announcement promises a table screenshot; let the next cron tick retry.
    _unmark(game, kind)


def _game_has_pending_attempts(game: Game) -> bool:
    return Attempt.manager.filter(game=game, status='Pending', skip=False).exists()


def _process_results(game: Game, now, stats: dict, results_png_cache: dict) -> None:
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
    podium = build_podium(
        data.get('team_to_place') or {},
        teams_order=data.get('teams_sorted') or [],
    )
    caption = format_game_results_announcement(game, podium)
    png = _fresh_tournament_results_png(game, results_png_cache)
    social_png = _tournament_results_social_png(game)
    if _mark_and_publish_social(
        game,
        TelegramGameAnnouncement.KIND_RESULTS,
        caption,
        png,
        filename='results-{}.png'.format(game.id),
        social_photo_bytes=social_png,
    ):
        stats['results'] += 1


def process_game_announcements(now=None) -> dict[str, int]:
    now = now or timezone.now()
    results_png_cache = {}
    stats = {
        'day_before': 0,
        'hour_before': 0,
        'start': 0,
        'end_soon_15': 0,
        'end': 0,
        'all_solved': 0,
        'no_coffins': 0,
        'results': 0,
        'admin_start_soon': 0,
    }

    for game in Game.objects.all():
        start = game.start_time
        end = game.end_time
        if start is None or end is None:
            continue

        if game_telegram_announce_enabled(game) and _should_consider_game(game, now):
            try:
                _process_day_before(game, now, stats)
            except Exception:
                logger.exception('day_before social announce failed for game %s', game.id)

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
                _process_all_solved(game, now, stats, results_png_cache)
            except Exception:
                logger.exception('all_solved announce failed for game %s', game.id)

            try:
                _process_no_coffins(game, now, stats, results_png_cache)
            except Exception:
                logger.exception('no_coffins announce failed for game %s', game.id)

            try:
                _process_results(game, now, stats, results_png_cache)
            except Exception:
                logger.exception('results announce failed for game %s', game.id)

        if _should_admin_start_soon(game, now):
            # DB unique (game, kind) — same as chat announcements. Do not use
            # django.core.cache: default LocMem is per-process, and EB cron starts
            # a fresh manage.py on every instance every minute.
            if _try_mark(game, TelegramGameAnnouncement.KIND_ADMIN_START_SOON):
                if notify_admin_game_lifecycle(game, 'start_soon'):
                    stats['admin_start_soon'] += 1
                else:
                    _unmark(game, TelegramGameAnnouncement.KIND_ADMIN_START_SOON)

    try:
        from games.telegram.ladder_channel import process_ladder_channel_tick

        ladder_stats = process_ladder_channel_tick(now=now)
        stats['ladder_scheduled'] = ladder_stats.get('scheduled', 0)
    except Exception:
        logger.exception('Ladder channel tick failed')
        stats['ladder_scheduled'] = 0

    # Алфавитка / задание недели: около полуночи МСК досэмплить буфер.
    stats['alphabetty_buffer_added'] = 0
    stats['week_task_buffer_added'] = 0
    try:
        from zoneinfo import ZoneInfo

        msk_now = now.astimezone(ZoneInfo('Europe/Moscow'))
        if msk_now.hour == 0 and msk_now.minute < 5:
            day_key = msk_now.date().isoformat()
            try:
                from games.support.services.alphabetty import ensure_future_buffer
                from games.alphabetty_daily import ALPHABETTY_GAME_ID

                ab_game = Game.objects.filter(pk=ALPHABETTY_GAME_ID).first()
                if ab_game and _try_mark(ab_game, f'alphabetty_buffer:{day_key}'):
                    buf = ensure_future_buffer(now=now)
                    stats['alphabetty_buffer_added'] = int(buf.get('added') or 0)
            except Exception:
                logger.exception('Alphabetty buffer ensure failed')
            try:
                from games.support.services.week_tasks import (
                    ensure_future_buffer as week_task_ensure_future_buffer,
                )
                from games.week_task_weekly import WEEK_TASK_GAME_ID

                wt_game = Game.objects.filter(pk=WEEK_TASK_GAME_ID).first()
                if wt_game and _try_mark(wt_game, f'week_task_buffer:{day_key}'):
                    wt_buf = week_task_ensure_future_buffer(now=now)
                    stats['week_task_buffer_added'] = int(wt_buf.get('added') or 0)
            except Exception:
                logger.exception('Week task buffer ensure failed')
    except Exception:
        logger.exception('Daily buffer ensure failed')

    try:
        from games.social.publish import process_social_queue_tick

        queue_stats = process_social_queue_tick(now=now)
        stats['social_queue'] = queue_stats
    except Exception:
        logger.exception('Social queue tick failed')
        stats['social_queue'] = {'errors': 1}

    return stats


def _should_admin_start_soon(game: Game, now) -> bool:
    start = game.get_visible_start_time()
    if start is None:
        return False
    window_start = start - timedelta(hours=1, minutes=5)
    window_end = start - timedelta(minutes=55)
    return window_start <= now <= window_end


def notify_admin_game_lifecycle(game, event: str) -> bool:
    from games.telegram.notify import format_admin_game_lifecycle_message

    return send_admin_message(format_admin_game_lifecycle_message(game, event))


def notify_admin_registration_milestone(game, count: int) -> None:
    from games.telegram.notify import format_admin_registration_milestone_message

    send_admin_message(format_admin_registration_milestone_message(game, count))
