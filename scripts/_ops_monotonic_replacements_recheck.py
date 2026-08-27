#!/usr/bin/env python
"""One-off monotonic recheck for selected replacements hub circles.

Dry-run by default. Set MONOTONIC_APPLY=1 to persist. Existing solved lines are
always unioned with lines accepted by the current checker, so no historical
credit can be lost when answer variants were replaced.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from games.check import CheckerFactory
from games.models import Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, Task


TG_NUMBERS = (154, 156, 137)
TARGET_USER_IDS = (456, 694)


def parse_state(raw):
    if not raw:
        return set()
    try:
        return {int(value) for value in (json.loads(raw).get('solved_lines') or [])}
    except (AttributeError, TypeError, ValueError):
        return set()


def dump_state(lines):
    solved = sorted(lines)
    return json.dumps({'solved_lines': solved, 'total': len(solved)}, ensure_ascii=False)


def actor_label(combo):
    if combo['user'] is not None:
        profile = getattr(combo['user'], 'profile', None)
        name = str(profile) if profile is not None else combo['user'].get_full_name()
        return 'user:{} ({})'.format(combo['user'].pk, name or combo['user'].username)
    if combo['team'] is not None:
        return 'team:{}'.format(combo['team'].pk)
    return 'anon:{}…'.format((combo['anon_key'] or '')[:8])


def rank(status):
    return {'Wrong': 0, 'Pending': 0, 'Partial': 1, 'Ok': 2}.get(status, 0)


def collect_combos(task, game):
    seen = set()
    combos = []
    for attempt in Attempt.manager.filter(task=task, game=game).select_related('team', 'user', 'user__profile'):
        key = (attempt.team_id, attempt.user_id, attempt.anon_key)
        if key in seen:
            continue
        seen.add(key)
        combos.append({
            'team': attempt.team,
            'user': attempt.user if attempt.user_id else None,
            'anon_key': attempt.anon_key,
        })
    return combos


def run():
    apply_changes = os.environ.get('MONOTONIC_APPLY') == '1'
    game = Game.objects.get(pk='replacements')
    checker_type = CheckerType.objects.get(pk='replacements_lines')
    factory = CheckerFactory()
    totals = defaultdict(int)
    new_credits = []
    completions = []
    target_rows = []
    regressions = []

    with transaction.atomic():
        for tg_number in TG_NUMBERS:
            link = GameTaskGroup.objects.select_related('task_group').get(game=game, number=str(tg_number))
            tasks = Task.objects.filter(
                task_group=link.task_group,
                is_removed=False,
                task_type='replacements_lines',
            ).order_by('number')
            for task in tasks:
                n_lines = task._replacements_lines_n_answer_rows()
                multiplier = Decimal(str(task.get_points()))
                if apply_changes:
                    Task.objects.select_for_update().only('pk').get(pk=task.pk)
                combos = collect_combos(task, game)
                print('\n=== Замены #{} task={} actors={} ==='.format(tg_number, task.pk, len(combos)))

                for combo in combos:
                    label = actor_label(combo)
                    attempts_qs = Attempt.manager.filter(
                        task=task,
                        game=game,
                        team=combo['team'],
                        user=combo['user'],
                        anon_key=combo['anon_key'],
                    ).order_by('time', 'id')
                    if apply_changes:
                        attempts_qs = attempts_qs.select_for_update()
                    attempts = list(attempts_qs)
                    totals['combos'] += 1
                    totals['attempts'] += len(attempts)

                    chain_qs = ChainTaskState.objects.filter(
                        task=task,
                        game=game,
                        team=combo['team'],
                        user=combo['user'],
                        anon_key=combo['anon_key'],
                    )
                    if apply_changes:
                        chain_qs = chain_qs.select_for_update()
                    rows = {row.game_mode: row for row in chain_qs}
                    before_final = {
                        mode: parse_state(row.state) for mode, row in rows.items()
                    }

                    states = {'general': set(), 'tournament': set()}
                    last_by_mode = {}
                    combo_changed = 0
                    combo_new = set()
                    for attempt in attempts:
                        mode = game.get_current_mode(attempt)
                        base = set(states[mode]) | parse_state(attempt.state)
                        checker = factory.create_checker(
                            checker_type,
                            task.checker_data or '',
                            dump_state(base) if base else None,
                        )
                        try:
                            result = checker.check(attempt.text, attempt)
                            checked = parse_state(result.state)
                            merged = base | checked
                            new_here = checked - base
                            new_status = 'Ok' if len(merged) >= n_lines else ('Partial' if merged else 'Wrong')
                            new_points = multiplier * len(merged)
                            new_skip = False
                            new_state = dump_state(merged)
                        except Exception as exc:
                            regressions.append((tg_number, label, attempt.pk, 'checker error: {}'.format(exc)))
                            merged = base
                            states[mode] = merged
                            last_by_mode[mode] = attempt
                            continue

                        old_lines = parse_state(attempt.state)
                        if not old_lines.issubset(merged):
                            regressions.append((tg_number, label, attempt.pk, 'lost solved lines'))
                        if new_points < Decimal(str(attempt.points or 0)):
                            regressions.append((tg_number, label, attempt.pk, 'points decrease'))
                        if rank(new_status) < rank(attempt.status):
                            regressions.append((tg_number, label, attempt.pk, 'status regression {}→{}'.format(attempt.status, new_status)))

                        changed = (
                            attempt.status != new_status
                            or Decimal(str(attempt.points or 0)) != new_points
                            or bool(attempt.skip) != new_skip
                            or parse_state(attempt.state) != merged
                        )
                        if changed:
                            combo_changed += 1
                            totals['attempts_changed'] += 1
                            if apply_changes:
                                Attempt.manager.filter(pk=attempt.pk).update(
                                    status=new_status,
                                    points=new_points,
                                    skip=new_skip,
                                    state=new_state,
                                )
                        for line in sorted(new_here):
                            key = (tg_number, task.pk, label, mode, line)
                            if key not in combo_new:
                                combo_new.add(key)
                                new_credits.append({
                                    'tg': tg_number,
                                    'task': task.pk,
                                    'actor': label,
                                    'mode': mode,
                                    'line': line,
                                    'attempt': attempt.pk,
                                    'text': attempt.text,
                                })
                        states[mode] = merged
                        last_by_mode[mode] = attempt

                    for mode, last_attempt in last_by_mode.items():
                        old_final = before_final.get(mode, set())
                        final_lines = states[mode] | old_final
                        states[mode] = final_lines
                        if not old_final.issubset(final_lines):
                            regressions.append((tg_number, label, last_attempt.pk, 'final chain lost solved lines'))
                        if len(old_final) < n_lines and len(final_lines) >= n_lines:
                            completions.append((tg_number, task.pk, label, mode, len(old_final), len(final_lines)))

                        # If a ChainTaskState carried credit absent from the last Attempt,
                        # preserve it on that last attempt as well.
                        last_lines = parse_state(last_attempt.state)
                        final_status = 'Ok' if len(final_lines) >= n_lines else ('Partial' if final_lines else 'Wrong')
                        final_points = multiplier * len(final_lines)
                        if final_lines != last_lines or rank(final_status) > rank(last_attempt.status) or final_points > Decimal(str(last_attempt.points or 0)):
                            totals['last_attempt_chain_merges'] += 1
                            if apply_changes:
                                Attempt.manager.filter(pk=last_attempt.pk).update(
                                    status=final_status,
                                    points=final_points,
                                    skip=False,
                                    state=dump_state(final_lines),
                                )

                        row = rows.get(mode)
                        if row is None and apply_changes:
                            row = ChainTaskState.objects.create(
                                task=task,
                                game=game,
                                game_mode=mode,
                                team=combo['team'],
                                user=combo['user'],
                                anon_key=combo['anon_key'],
                            )
                        chain_changed = row is None or parse_state(row.state) != final_lines or row.last_attempt_id != last_attempt.pk
                        if chain_changed:
                            totals['chains_changed'] += 1
                            if apply_changes:
                                row.state = dump_state(final_lines)
                                row.last_attempt = last_attempt
                                row.save(update_fields=['state', 'last_attempt', 'updated_at'])

                    if combo_changed:
                        print('  {}: {} attempt(s) improve/update'.format(label, combo_changed))
                    if combo['user'] is not None and combo['user'].pk in TARGET_USER_IDS:
                        for mode in ('general', 'tournament'):
                            if mode in last_by_mode or mode in before_final:
                                target_rows.append({
                                    'tg': tg_number,
                                    'task': task.pk,
                                    'user': combo['user'].pk,
                                    'mode': mode,
                                    'before': sorted(before_final.get(mode, set())),
                                    'after': sorted(states.get(mode, set()) | before_final.get(mode, set())),
                                    'n_lines': n_lines,
                                })

        if regressions or not apply_changes:
            transaction.set_rollback(True)

    print('\n========== MONOTONIC {} SUMMARY =========='.format('APPLY' if apply_changes else 'DRY-RUN'))
    print('TG: {}'.format(list(TG_NUMBERS)))
    print('Actor+task combos: {}'.format(totals['combos']))
    print('Attempts scanned: {}'.format(totals['attempts']))
    print('Attempts updated: {}'.format(totals['attempts_changed']))
    print('Chain rows updated: {}'.format(totals['chains_changed']))
    print('New checker credits: {}'.format(len(new_credits)))
    print('New completions: {}'.format(len(completions)))
    print('Regressions/errors: {}'.format(len(regressions)))
    for item in new_credits:
        print('  CREDIT TG{tg} {actor} line={line} attempt={attempt}'.format(**item))
        print('    text: {}'.format(item['text'][:240]))
    for item in completions:
        print('  COMPLETE TG{} task={} {} mode={} {}→{}'.format(*item))
    for item in target_rows:
        print('  TARGET TG{tg} user={user} mode={mode} solved={before}→{after} / {n_lines}'.format(**item))
    for item in regressions:
        print('  REGRESSION/ERROR TG{} {} attempt={}: {}'.format(*item))

    if regressions:
        raise SystemExit(2)
    if apply_changes:
        print('COMMITTED')
    else:
        print('ROLLED BACK / READ-ONLY')


run()
