#!/usr/bin/env python
"""
Перепроверка посылок replacements_lines по выбранным номерам «Замен» (GameTaskGroup.number).

Снимает снимок attempt/ChainTaskState до recheck_chain_task, перепроигрывает цепочку,
печатает что изменилось (в т.ч. Wrong→Partial/Ok, рост solved_lines / points).

Запуск на проде:
  ./scripts/eb_run.sh scripts/recheck_replacements_tg_diff.py --tg 51 42 110 125
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

from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Task  # noqa: E402
from games.recheck import recheck_chain_task  # noqa: E402


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


def _attempt_snapshot(attempt):
    return {
        'id': attempt.id,
        'status': attempt.status,
        'points': str(attempt.points),
        'skip': attempt.skip,
        'state': _parse_chain_state(attempt.state),
        'text': (attempt.text or '')[:120],
        'time': str(attempt.time),
    }


def _chain_rows_snapshot(task, team, user, anon_key, game):
    rows = {}
    for row in ChainTaskState.objects.filter(
        task=task, team=team, user=user, anon_key=anon_key, game=game,
    ):
        rows[row.game_mode] = {
            'state': _parse_chain_state(row.state),
            'last_attempt_id': row.last_attempt_id,
        }
    return rows


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
    qs = Attempt.manager.select_related('team', 'user').filter(
        task=task, game=game,
    )
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tg', nargs='+', type=int, required=True, help='GameTaskGroup.number')
    parser.add_argument('--dry-run', action='store_true', help='Only report, no recheck')
    args = parser.parse_args()

    game = Game.objects.get(id='replacements')
    improvements = []
    regressions = []
    total_combos = 0
    total_attempts = 0

    for tg_num, task in iter_tasks_for_tg_numbers(args.tg):
        combos = collect_actor_combos(task, game)
        print(f'\n=== Замены #{tg_num} task={task.id} number={task.number!r} actors={len(combos)} ===')
        for combo in combos:
            total_combos += 1
            actor = _actor_label(combo['team'], combo['user'], combo['anon_key'])

            attempts_before = {
                a.id: _attempt_snapshot(a)
                for a in Attempt.manager.get_all_attempts(
                    combo['team'], task, exclude_skip=False,
                    user=combo['user'], anon_key=combo['anon_key'], game=game,
                )
            }
            total_attempts += len(attempts_before)
            chain_before = _chain_rows_snapshot(
                task, combo['team'], combo['user'], combo['anon_key'], game,
            )

            if args.dry_run:
                print(f'  [dry-run] {actor}: {len(attempts_before)} attempts')
                continue

            recheck_chain_task(
                task=task,
                team=combo['team'],
                user=combo['user'],
                anon_key=combo['anon_key'],
                game=game,
            )

            attempts_after = {
                a.id: _attempt_snapshot(a)
                for a in Attempt.manager.get_all_attempts(
                    combo['team'], task, exclude_skip=False,
                    user=combo['user'], anon_key=combo['anon_key'], game=game,
                )
            }
            chain_after = _chain_rows_snapshot(
                task, combo['team'], combo['user'], combo['anon_key'], game,
            )

            for aid, before in attempts_before.items():
                after = attempts_after.get(aid)
                if not after:
                    continue
                improved = False
                regressed = False
                changes = []

                if before['skip'] and not after['skip']:
                    changes.append('unskipped')
                    improved = True
                elif not before['skip'] and after['skip']:
                    changes.append('skipped')
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
                    entry = {
                        'tg': tg_num,
                        'task_id': task.id,
                        'actor': actor,
                        'attempt_id': aid,
                        'changes': changes,
                        'text': after['text'],
                    }
                    if improved and not regressed:
                        improvements.append(entry)
                    elif regressed:
                        regressions.append(entry)
                    else:
                        improvements.append(entry)

            for mode in sorted(set(chain_before) | set(chain_after)):
                b = chain_before.get(mode, {'state': {'solved_lines': [], 'total': 0}})
                a = chain_after.get(mode, {'state': {'solved_lines': [], 'total': 0}})
                if b['state'] != a['state']:
                    new_lines = set(a['state']['solved_lines']) - set(b['state']['solved_lines'])
                    if new_lines:
                        improvements.append({
                            'tg': tg_num,
                            'task_id': task.id,
                            'actor': actor,
                            'attempt_id': None,
                            'changes': [f'ChainTaskState[{mode}] +solved_lines {sorted(new_lines)}'],
                            'text': '',
                        })

    print('\n========== SUMMARY ==========')
    print(f'Task groups: {args.tg}')
    print(f'Actor+task combos: {total_combos}')
    print(f'Attempts scanned: {total_attempts}')
    print(f'Improvements: {len(improvements)}')
    print(f'Regressions: {len(regressions)}')

    if improvements:
        print('\n--- Зачтено / улучшено (раньше хуже или не засчитывалось) ---')
        for row in improvements:
            aid = row['attempt_id'] or 'chain'
            print(
                f"  TG{row['tg']} task={row['task_id']} {row['actor']} "
                f"attempt={aid}: {', '.join(row['changes'])}"
            )
            if row['text']:
                print(f"    text: {row['text']}")

    if regressions:
        print('\n--- Регрессии (стало хуже) ---')
        for row in regressions:
            print(
                f"  TG{row['tg']} task={row['task_id']} {row['actor']} "
                f"attempt={row['attempt_id']}: {', '.join(row['changes'])}"
            )

    return 1 if regressions else 0


if __name__ == '__main__':
    sys.exit(main())
