#!/usr/bin/env python
"""
Симуляция перепроверки replacements_lines (без записи в БД).

Перепроигрывает цепочку посылок в памяти (как recheck_chain_task) и сравнивает
с сохранёнными attempt.status / points / state и ChainTaskState.

Запуск на проде (read-only):
  ./scripts/eb_run.sh scripts/simulate_replacements_tg_recheck.py --tg 51 42 110 125
"""
from __future__ import annotations

import argparse
import json
import sys

import django

if __name__ == '__main__':
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()

from decimal import Decimal  # noqa: E402

from games.check import CheckerFactory  # noqa: E402
from games.models import Attempt, ChainTaskState, CheckerType, Game, GameTaskGroup, Task  # noqa: E402


def _actor_label(team, user, anon_key):
    if user:
        return f'user:{user.pk}'
    if anon_key:
        return f'anon:{anon_key[:8]}…'
    if team:
        return f'team:{team.pk} ({getattr(team, "visible_name", team.name)})'
    return 'unknown'


def _parse_chain_state(raw):
    if not raw:
        return {'solved_lines': [], 'total': 0}
    try:
        data = json.loads(raw)
        return {
            'solved_lines': sorted(data.get('solved_lines') or []),
            'total': int(data.get('total') or 0),
        }
    except (TypeError, ValueError):
        return {'solved_lines': [], 'total': 0}


def _attempt_view(attempt):
    return {
        'id': attempt.id,
        'status': attempt.status,
        'points': str(attempt.points),
        'skip': attempt.skip,
        'state': _parse_chain_state(attempt.state),
        'text': (attempt.text or '')[:120],
        'time': str(attempt.time),
    }


def iter_tasks_for_tg_numbers(tg_numbers):
    game = Game.objects.get(id='replacements')
    for n in tg_numbers:
        link = GameTaskGroup.objects.select_related('task_group').get(game=game, number=n)
        tg = link.task_group
        for task in Task.objects.filter(
            task_group=tg, is_removed=False, task_type='replacements_lines',
        ).order_by('number'):
            yield n, task


def collect_actor_combos(task, game):
    seen = set()
    combos = []
    qs = Attempt.manager.select_related('team', 'user').filter(task=task, game=game)
    for attempt in qs.iterator():
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


def simulate_chain(task, team, user, anon_key, game):
    checker_type = CheckerType.objects.get(id='replacements_lines')
    checker_data = task.checker_data or ''
    states = {'general': None, 'tournament': None}
    simulated = {}
    chain_sim = {'general': None, 'tournament': None}

    attempts = Attempt.manager.get_all_attempts(
        team, task, exclude_skip=False, user=user, anon_key=anon_key, game=game,
    )

    for attempt in attempts:
        mode = game.get_current_mode(attempt)
        last_state = states[mode]
        try:
            checker = CheckerFactory().create_checker(checker_type, checker_data, last_state)
            result = checker.check(attempt.text, attempt)
            sim = {
                'status': result.status,
                'points': str(Decimal(str(result.points or 0)) * task.get_points()),
                'skip': False,
                'state': _parse_chain_state(result.state),
            }
            states[mode] = result.state
            chain_sim[mode] = result.state
        except Exception as exc:
            sim = {
                'status': attempt.status,
                'points': str(attempt.points),
                'skip': True,
                'state': _parse_chain_state(last_state),
                'error': str(exc),
            }
        simulated[attempt.id] = sim

    return simulated, {
        mode: _parse_chain_state(raw) for mode, raw in chain_sim.items() if raw is not None
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tg', nargs='+', type=int, required=True)
    args = parser.parse_args()

    game = Game.objects.get(id='replacements')
    improvements = []
    regressions = []
    mismatches_chain = []
    total_combos = 0
    total_attempts = 0
    total_ok = 0

    for tg_num, task in iter_tasks_for_tg_numbers(args.tg):
        combos = collect_actor_combos(task, game)
        print(f'\n=== Замены #{tg_num} task={task.id} number={task.number!r} actors={len(combos)} ===')
        for combo in combos:
            total_combos += 1
            actor = _actor_label(combo['team'], combo['user'], combo['anon_key'])
            attempts = Attempt.manager.get_all_attempts(
                combo['team'], task, exclude_skip=False,
                user=combo['user'], anon_key=combo['anon_key'], game=game,
            )
            total_attempts += len(attempts)
            if not attempts:
                continue

            stored = {a.id: _attempt_view(a) for a in attempts}
            simulated, sim_chain = simulate_chain(
                task, combo['team'], combo['user'], combo['anon_key'], game,
            )

            stored_chain = {}
            for row in ChainTaskState.objects.filter(
                task=task,
                team=combo['team'],
                user=combo['user'],
                anon_key=combo['anon_key'],
                game=game,
            ):
                stored_chain[row.game_mode] = _parse_chain_state(row.state)

            combo_ok = True
            for aid, before in stored.items():
                after = simulated.get(aid)
                if not after:
                    continue
                improved = False
                regressed = False
                changes = []

                if before['skip'] and not after['skip']:
                    changes.append('unskipped')
                    improved = True
                elif not before['skip'] and after['skip']:
                    changes.append('would_skip')
                    regressed = True

                if before['status'] != after['status']:
                    changes.append(f"status {before['status']}→{after['status']}")
                    if before['status'] == 'Wrong' and after['status'] in ('Partial', 'Ok'):
                        improved = True
                    elif before['status'] in ('Partial', 'Ok') and after['status'] == 'Wrong':
                        regressed = True
                    elif before['status'] == 'Partial' and after['status'] == 'Ok':
                        improved = True

                if before['points'] != after['points']:
                    changes.append(f"points {before['points']}→{after['points']}")
                    try:
                        if float(after['points']) > float(before['points']):
                            improved = True
                        elif float(after['points']) < float(before['points']):
                            regressed = True
                    except ValueError:
                        pass

                b_state = before['state']
                a_state = after['state']
                if b_state != a_state:
                    new_lines = set(a_state['solved_lines']) - set(b_state['solved_lines'])
                    lost_lines = set(b_state['solved_lines']) - set(a_state['solved_lines'])
                    if new_lines:
                        changes.append(f'+solved_lines {sorted(new_lines)}')
                        improved = True
                    if lost_lines:
                        changes.append(f'-solved_lines {sorted(lost_lines)}')
                        regressed = True
                    if b_state['total'] != a_state['total']:
                        changes.append(f"total {b_state['total']}→{a_state['total']}")

                if changes:
                    combo_ok = False
                    entry = {
                        'tg': tg_num,
                        'task_id': task.id,
                        'actor': actor,
                        'attempt_id': aid,
                        'changes': changes,
                        'text': before['text'],
                    }
                    if improved and not regressed:
                        improvements.append(entry)
                    elif regressed:
                        regressions.append(entry)
                    else:
                        improvements.append(entry)

            for mode in sorted(set(stored_chain) | set(sim_chain)):
                b = stored_chain.get(mode, {'solved_lines': [], 'total': 0})
                a = sim_chain.get(mode, {'solved_lines': [], 'total': 0})
                if b != a:
                    combo_ok = False
                    new_lines = set(a['solved_lines']) - set(b['solved_lines'])
                    lost_lines = set(b['solved_lines']) - set(a['solved_lines'])
                    mismatches_chain.append({
                        'tg': tg_num,
                        'task_id': task.id,
                        'actor': actor,
                        'mode': mode,
                        'stored': b,
                        'simulated': a,
                        'new_lines': sorted(new_lines),
                        'lost_lines': sorted(lost_lines),
                    })

            if combo_ok:
                total_ok += 1
            else:
                print(f'  DIFF {actor}: {len(attempts)} attempts')

    print('\n========== SUMMARY (read-only simulation) ==========')
    print(f'Task groups: {args.tg}')
    print(f'Actor+task combos: {total_combos} (unchanged: {total_ok})')
    print(f'Attempts scanned: {total_attempts}')
    print(f'Would improve: {len(improvements)}')
    print(f'Would regress: {len(regressions)}')
    print(f'ChainTaskState mismatches: {len(mismatches_chain)}')

    if improvements:
        print('\n--- Зачтётся / улучшится после recheck ---')
        for row in improvements:
            print(
                f"  TG{row['tg']} task={row['task_id']} {row['actor']} "
                f"attempt={row['attempt_id']}: {', '.join(row['changes'])}"
            )
            if row['text']:
                print(f"    text: {row['text']}")

    if mismatches_chain:
        print('\n--- ChainTaskState расходится с симуляцией ---')
        for row in mismatches_chain:
            print(
                f"  TG{row['tg']} task={row['task_id']} {row['actor']} mode={row['mode']}: "
                f"stored={row['stored']} sim={row['simulated']}"
            )
            if row['new_lines']:
                print(f"    +lines: {row['new_lines']}")
            if row['lost_lines']:
                print(f"    -lines: {row['lost_lines']}")

    if regressions:
        print('\n--- Регрессии ---')
        for row in regressions:
            print(
                f"  TG{row['tg']} task={row['task_id']} {row['actor']} "
                f"attempt={row['attempt_id']}: {', '.join(row['changes'])}"
            )

    return 0


if __name__ == '__main__':
    sys.exit(main())
