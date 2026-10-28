"""Finish Цензурки solves stuck behind initially open title words.

Before 065fb83d, title words open from the start (pronouns, numerals) were
required for the win, so players who opened every hidden title word were left
with an unsolved game. For each such actor this marks the first attempt that
opened the last hidden title word as the win, rewinds the chain state to that
moment and runs the normal completion (timing freeze, completion record).

Dry run by default; pass --apply to write.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from games.daily.completion.completion_coordinator import complete_logical_game
from games.daily.timing.session import recovered_timing_ms_from_events
from games.models import (
    Attempt,
    ChainTaskState,
    DailySolveTiming,
    DailyTimingEvent,
    PlayerCompletedGame,
    Task,
)
from games.puzzles.censorly.ids import CENSORLY_TASK_TYPE
from games.puzzles.censorly.play import (
    _title_won,
    dump_state,
    load_state,
    points_for_state,
    puzzle_from_task,
)
from games.puzzles.censorly.text.tokenize import content_token_open_at_start

SOURCE = 'censorly_open_title_fix'


def _has_open_title_word(payload):
    return any(
        tok.get('kind') == 'content' and content_token_open_at_start(tok)
        for tok in payload.get('title_tokens') or []
    )


def _actor(row):
    return {
        'team': row.team,
        'user': row.user,
        'anon_key': row.anon_key,
        'replay_slot': row.replay_slot,
    }


def _winning_attempt(row, payload):
    """First attempt of this actor whose state opens every hidden title word."""
    attempts = Attempt.manager.filter(
        task=row.task, game=row.game, **_actor(row),
    ).order_by('time', 'id')
    for attempt in attempts:
        state = load_state(attempt.state)
        if _title_won(payload, set(state['revealed_lemmas'])):
            return attempt, state, attempts.filter(time__gt=attempt.time).count()
    return None, None, 0


def _active_ms_at(row, when):
    """Active solve time at *when*, rebuilt from the timing event ledger."""
    events = list(DailyTimingEvent.objects.filter(
        game=row.game, task_group=row.task.task_group, occurred_at__lte=when,
        **_actor(row),
    ).order_by('occurred_at', 'pk'))
    if not events:
        return None
    return recovered_timing_ms_from_events(events, completed_at=when)


class Command(BaseCommand):
    help = 'Mark Цензурки games stuck behind initially open title words as solved.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write changes (default: dry run).')

    def handle(self, *args, **options):
        apply = options['apply']
        fixed = 0
        for task in Task.objects.filter(task_type=CENSORLY_TASK_TYPE).select_related('task_group'):
            payload = puzzle_from_task(task, persist=False)
            if not payload or not _has_open_title_word(payload):
                continue
            rows = ChainTaskState.objects.filter(
                task=task, game_mode='general',
            ).select_related('game', 'user', 'replay_slot')
            for row in rows:
                state = load_state(row.state)
                if state['won'] or not _title_won(payload, set(state['revealed_lemmas'])):
                    continue
                attempt, win_state, later = _winning_attempt(row, payload)
                actor_label = row.user.username if row.user else 'anon:{}'.format(row.anon_key)
                if attempt is None:
                    self.stdout.write(self.style.WARNING(
                        'task={} {}: no winning attempt found, skipped'.format(task.pk, actor_label),
                    ))
                    continue
                win_state['won'] = True
                active_ms = _active_ms_at(row, attempt.time)
                self.stdout.write(
                    'task={} game={} {} attempt={} at {} points={} active_ms={} '
                    'later_attempts={}{}'.format(
                        task.pk, row.game_id, actor_label, attempt.pk,
                        attempt.time.isoformat(), points_for_state(win_state, task=task),
                        active_ms, later, ' replay' if row.replay_slot_id else '',
                    )
                )
                if apply:
                    self._fix(row, task, attempt, win_state, active_ms)
                fixed += 1
        verb = 'Fixed' if apply else 'Would fix'
        self.stdout.write(self.style.SUCCESS('{} {} game(s)'.format(verb, fixed)))
        if not apply and fixed:
            self.stdout.write('Dry run; pass --apply to write.')

    def _fix(self, row, task, attempt, win_state, active_ms):
        with transaction.atomic():
            row = ChainTaskState.objects.select_for_update().get(pk=row.pk)
            attempt.status = 'Ok'
            attempt.points = points_for_state(win_state, task=task)
            attempt.state = dump_state(win_state)
            if active_ms is not None and not row.replay_slot_id:
                attempt.active_time_ms = active_ms
            attempt.save(update_fields=['status', 'points', 'state', 'active_time_ms'])
            row.state = dump_state(win_state)
            row.last_attempt = attempt
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])

        game = row.game
        mode = game.get_current_mode(Attempt(time=attempt.time))
        completion = complete_logical_game(
            actor={'team': None, 'user': row.user, 'anon_key': row.anon_key},
            game=game,
            task_group=task.task_group,
            task=task,
            replay_slot=row.replay_slot,
            run_id=row.replay_slot.run_id if row.replay_slot_id else None,
            analytics_user=row.user,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=mode,
            source=SOURCE,
            now=attempt.time,
        )
        if completion is None or completion.get('replay'):
            return
        if active_ms is not None:
            # The clock kept running while the player was stuck; freeze it at
            # the moment the game was actually solved.
            DailySolveTiming.objects.filter(
                game=game, task_group=task.task_group, replay_slot=None,
                **({'user': row.user} if row.user else {'anon_key': row.anon_key}),
            ).update(accumulated_ms=active_ms, frozen_ms=active_ms, completed_at=attempt.time)
        record = completion.get('record')
        if record is not None and completion.get('created'):
            PlayerCompletedGame.objects.filter(pk=record.pk).update(
                completed_at=attempt.time, is_backfilled=True,
            )
