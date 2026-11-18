"""Replay one Цензурка's guesses through the current matcher.

After a matcher fix (for example, spelling variants of a name now share one
root) a guess that missed may now open words. Each actor's attempts are
replayed in order, exactly as ``apply_guess``/``apply_hint`` would treat them
today:

* a guess that now opens a word becomes a hit (or the win);
* a later guess that would now only hit already open words is dropped, like
  an ``already_open`` guess that never creates an attempt;
* attempts after the (possibly earlier) win are dropped.

Only ``--lemma`` is rechecked: for every other article lemma a guess opens
exactly what it opened when it was played, so later matcher changes elsewhere
do not leak into old results.

Dry run by default; ``--apply`` writes and saves a JSON backup of every
touched row first.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.forms.models import model_to_dict

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
    _guess_matches,
    _matching_token_ids,
    _title_won,
    default_state,
    dump_state,
    hint_count,
    load_state,
    points_for_state,
    puzzle_from_task,
)
from games.puzzles.censorly.text.normalize import lemma_of

SOURCE = 'censorly_guess_recheck'
HINT_PREFIX = '#hint:'


def _actor(row):
    return {
        'team': row.team,
        'user': row.user,
        'anon_key': row.anon_key,
        'replay_slot': row.replay_slot,
    }


def _label(row):
    who = row.user.username if row.user else 'anon:{}'.format((row.anon_key or '')[:8])
    return who + (' replay' if row.replay_slot_id else '')


def _active_ms_at(row, when):
    events = list(DailyTimingEvent.objects.filter(
        game=row.game, task_group=row.task.task_group, occurred_at__lte=when,
        **_actor(row),
    ).order_by('occurred_at', 'pk'))
    if not events:
        return None
    return recovered_timing_ms_from_events(events, completed_at=when)


class Replay:
    """New outcome of one actor's attempts."""

    def __init__(self, row, payload, task, allowed):
        self.row = row
        self.kept = []      # (attempt, state, status)
        self.dropped = []   # attempts
        self.foreign = []   # differences outside ``allowed``
        self.changed = False
        state = default_state()
        old_revealed = set()
        attempts = list(Attempt.manager.filter(
            task=task, game=row.game, **_actor(row),
        ).order_by('time', 'id'))
        self.attempts = attempts
        self.old_won_attempt = next((a for a in attempts if a.status == 'Ok'), None)
        for attempt in attempts:
            old_state = load_state(attempt.state)
            old_opened = set(old_state['revealed_lemmas']) - old_revealed
            old_revealed = set(old_state['revealed_lemmas'])
            text = attempt.text or ''
            if state['won']:
                self._drop(attempt)
                continue
            revealed = set(state['revealed_lemmas'])
            if text.startswith(HINT_PREFIX):
                lemma = text[len(HINT_PREFIX):]
                if lemma in revealed:
                    self._drop(attempt)
                    continue
                state = dict(state)
                revealed.add(lemma)
                state['revealed_lemmas'] = sorted(revealed)
                state['last_lemma'] = lemma
                state['hints_taken'] = hint_count(state) + 1
                state['hinted_lemmas'] = sorted(set(state['hinted_lemmas']) | {lemma})
                opened = {lemma}
            else:
                guess_lemma = lemma_of(text)
                matched, _report = _guess_matches(
                    payload, text, guess_lemma, task=task, include_report=False,
                )
                # Outside ``allowed`` the guess keeps what it opened when it
                # was played; other matcher changes since then are ignored.
                matched = (old_opened - allowed) | (matched & allowed)
                opened = matched - revealed
                if matched and not opened:
                    self._drop(attempt)
                    continue
                hits = 0
                primary = ''
                if opened:
                    first, token_ids = _matching_token_ids(payload, opened)
                    primary = guess_lemma if guess_lemma in opened else first
                    hits = sum(len(ids) for ids in token_ids.values())
                state = dict(state)
                revealed |= opened
                state['revealed_lemmas'] = sorted(revealed)
                state['guesses'] = list(state['guesses']) + [{
                    'word': text, 'lemma': primary or guess_lemma, 'hits': hits,
                }]
                state['last_lemma'] = primary if hits else ''
            if opened != old_opened:
                diff = opened ^ old_opened
                if not diff <= allowed:
                    self.foreign.append((attempt, sorted(diff)))
                self.changed = True
            state['won'] = _title_won(payload, revealed)
            if state['won']:
                status = 'Ok'
            elif text.startswith(HINT_PREFIX):
                status = 'Partial'
            else:
                status = 'Partial' if state['guesses'][-1]['hits'] else 'Wrong'
            if status != attempt.status:
                self.changed = True
            self.kept.append((attempt, state, status))
        self.state = state
        self.new_won_attempt = next((a for a, _s, st in self.kept if st == 'Ok'), None)

    def _drop(self, attempt):
        self.dropped.append(attempt)
        self.changed = True


class Command(BaseCommand):
    help = 'Replay one Цензурка task through the current matcher (dry run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--task', type=int, required=True)
        parser.add_argument(
            '--lemma', action='append', required=True,
            help='Article lemma whose opening may change (repeatable).',
        )
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--backup', type=Path, help='JSON backup path (required with --apply).')

    def handle(self, *args, **options):
        task = Task.objects.select_related('task_group').get(pk=options['task'])
        if task.task_type != CENSORLY_TASK_TYPE:
            raise CommandError('task {} is not a Цензурка'.format(task.pk))
        if options['apply'] and not options['backup']:
            raise CommandError('--apply needs --backup')
        payload = puzzle_from_task(task, persist=False)
        allowed = set(options['lemma'])
        # Not select_related('task'): that repeats the multi-megabyte article
        # in every joined row (90 states took 62 s on production).
        rows = ChainTaskState.objects.filter(task=task).select_related(
            'game', 'user', 'replay_slot',
        ).order_by('pk')
        replays = []
        skipped = 0
        for row in rows:
            row.task = task
            replay = Replay(row, payload, task, allowed)
            if not replay.changed:
                continue
            old_n = sum(1 for a in replay.attempts if not (a.text or '').startswith(HINT_PREFIX))
            new_n = len(replay.state['guesses'])
            line = '{} {}: attempts {} -> {}, dropped {}, won {} -> {}{}'.format(
                row.pk, _label(row), old_n, new_n, len(replay.dropped),
                bool(replay.old_won_attempt), bool(replay.new_won_attempt),
                '' if replay.old_won_attempt == replay.new_won_attempt else ' (win moved)',
            )
            if replay.foreign:
                skipped += 1
                self.stdout.write(self.style.WARNING('SKIP ' + line + ' foreign diffs: {}'.format(
                    [(a.text, d) for a, d in replay.foreign],
                )))
                continue
            self.stdout.write(line)
            replays.append(replay)
        self.stdout.write(self.style.SUCCESS('{} actor(s) to change, {} skipped'.format(len(replays), skipped)))
        if not options['apply']:
            self.stdout.write('Dry run; pass --apply --backup FILE to write.')
            return
        self._backup(options['backup'], replays)
        for replay in replays:
            self._apply(replay, task)
        self.stdout.write(self.style.SUCCESS('Applied to {} actor(s)'.format(len(replays))))

    def _backup(self, path, replays):
        data = []
        for replay in replays:
            data.append({
                'chain_task_state': {
                    'id': replay.row.pk, 'state': replay.row.state,
                    'last_attempt_id': replay.row.last_attempt_id,
                },
                'attempts': [
                    {**model_to_dict(a), 'time': a.time.isoformat() if a.time else None,
                     'points': str(a.points)}
                    for a in replay.attempts
                ],
            })
        path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=1), encoding='utf-8')
        self.stdout.write('Backup: {}'.format(path))

    def _apply(self, replay, task):
        row = replay.row
        new_win = replay.new_won_attempt
        win_moved = new_win is not None and new_win != replay.old_won_attempt
        active_ms = _active_ms_at(row, new_win.time) if win_moved else None
        with transaction.atomic():
            ChainTaskState.objects.select_for_update().get(pk=row.pk)
            for attempt, state, status in replay.kept:
                attempt.state = dump_state(state)
                attempt.status = status
                attempt.points = points_for_state(state, task=task) if status == 'Ok' else Decimal('0')
                fields = ['state', 'status', 'points']
                if attempt is new_win and win_moved and active_ms is not None and not row.replay_slot_id:
                    attempt.active_time_ms = active_ms
                    fields.append('active_time_ms')
                attempt.save(update_fields=fields)
            last = replay.kept[-1][0] if replay.kept else None
            row.state = dump_state(replay.state)
            row.last_attempt = last
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])
            Attempt.manager.filter(pk__in=[a.pk for a in replay.dropped]).delete()
        if not win_moved or row.replay_slot_id:
            return
        actor_filter = {'user': row.user} if row.user else {'anon_key': row.anon_key}
        if replay.old_won_attempt is None:
            game = row.game
            complete_logical_game(
                actor={'team': None, 'user': row.user, 'anon_key': row.anon_key},
                game=game, task_group=task.task_group, task=task,
                replay_slot=None, run_id=None, analytics_user=row.user,
                result=PlayerCompletedGame.RESULT_SOLVED,
                mode=game.get_current_mode(Attempt(time=new_win.time)),
                source=SOURCE, now=new_win.time,
            )
        if active_ms is not None:
            DailySolveTiming.objects.filter(
                game=row.game, task_group=task.task_group, replay_slot=None, **actor_filter,
            ).update(accumulated_ms=active_ms, frozen_ms=active_ms, completed_at=new_win.time)
        PlayerCompletedGame.objects.filter(
            game=row.game, task_group=task.task_group, **actor_filter,
        ).update(completed_at=new_win.time)
