#!/usr/bin/env python
"""
Проверка всех заданий «Замены» (replacements_lines) на проде:
1) checker с каноническими ответами из БД;
2) HTTP POST /send_attempt/ как аноним (опционально --http).

Запуск на EB:
  ./scripts/eb_run.sh scripts/verify_replacements_prod.py
Локально с HTTP:
  ../venv/interoves_django/bin/python scripts/verify_replacements_prod.py --http
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

import django

if __name__ == '__main__':
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()

from django.test import RequestFactory  # noqa: E402

from games.check import CheckerFactory  # noqa: E402
from games.models import Attempt, CheckerType, Game, GameTaskGroup, Task  # noqa: E402
from games.replacements_lines import (  # noqa: E402
    parse_replacements_lines_text,
    task_replacements_canonical_answer_row,
)
from games.views.attempt_views import process_send_attempt  # noqa: E402

BASE_URL = 'https://interoves.com'


def iter_replacements_line_tasks():
    game = Game.objects.get(id='replacements')
    for link in GameTaskGroup.objects.filter(game=game).order_by('number'):
        tg = link.task_group
        for task in Task.objects.filter(task_group=tg, is_removed=False).order_by('number'):
            if task.task_type == 'replacements_lines':
                yield link, task


def checker_ok(task, line_index, answers):
    checker_type = CheckerType.objects.get(id='replacements_lines')
    checker_data = (task.checker_data or '').strip()
    checker = CheckerFactory().create_checker(checker_type, checker_data, None)
    payload = json.dumps({'line_index': line_index, 'answers': answers}, ensure_ascii=False)
    attempt = Attempt(text=payload, task=task, game=GameTaskGroup.resolve_game_for_task(task))
    result = checker.check(payload, attempt)
    return result.status in ('Ok', 'Partial') and result.comment != 'Нет данных для проверки'


def http_submit_line(session, task_id, game_id, line_index, answers, csrf, anon_key):
    import requests

    url = f'{BASE_URL}/send_attempt/{task_id}/'
    data = {
        'csrfmiddlewaretoken': csrf,
        'game_id': game_id,
        'anon_key': anon_key,
        'line_index': str(line_index),
    }
    for i, a in enumerate(answers):
        data[f'answers[{i}]'] = a
    # Django also accepts answers[] list
    resp = session.post(
        url,
        data={
            **data,
            'answers': json.dumps(answers, ensure_ascii=False),
        },
        headers={'Referer': f'{BASE_URL}/games/replacements/'},
        timeout=60,
    )
    try:
        body = resp.json()
    except Exception:
        body = {'raw': resp.text[:500]}
    return resp.status_code, body


def fetch_csrf_and_tasks_http(session, tg_number):
    import requests

    url = f'{BASE_URL}/games/replacements/{tg_number}/'
    r = session.get(url, timeout=60)
    r.raise_for_status()
    html = r.text
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    csrf = m.group(1) if m else session.cookies.get('csrftoken', '')
    game_m = re.search(r'name="game_id" value="([^"]+)"', html)
    game_id = game_m.group(1) if game_m else 'replacements'
    task_ids = sorted(set(int(x) for x in re.findall(r'data-task-id="(\d+)"', html)))
    repl_ids = []
    for tid in task_ids:
        if 'repl-form-' + str(tid) in html or f'repl-form-{tid}-' in html:
            repl_ids.append(tid)
    return csrf, game_id, repl_ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--http', action='store_true', help='Also hit live site as anonymous')
    args = parser.parse_args()

    failures = []
    checked = 0

    for link, task in iter_replacements_line_tasks():
        parsed = parse_replacements_lines_text(
            task.text, (task.checker_data or '').strip() or None
        )
        n_lines = len(parsed['right_tokens'])
        for li in range(n_lines):
            answers = task_replacements_canonical_answer_row(task, li)
            if not answers:
                failures.append(
                    (link.number, task.id, li, 'no_canonical', [])
                )
                continue
            checked += 1
            if not checker_ok(task, li, answers):
                failures.append(
                    (link.number, task.id, li, 'checker_fail', answers)
                )

    print(f'Checker: {checked} lines, {len(failures)} failures')
    for row in failures[:30]:
        print(' FAIL', row)
    if len(failures) > 30:
        print(f' ... and {len(failures) - 30} more')

    if not args.http:
        return 1 if failures else 0

    import requests

    session = requests.Session()
    session.headers['User-Agent'] = 'interoves-verify-replacements/1.0'
    anon_key = str(uuid.uuid4())
    http_failures = []
    http_checked = 0

    tg_numbers = sorted(
        {link.number for link, _ in iter_replacements_line_tasks()}
    )
    task_by_id = {t.id: (link, t) for link, t in iter_replacements_line_tasks()}

    for tg_num in tg_numbers:
        try:
            csrf, game_id, repl_ids = fetch_csrf_and_tasks_http(session, tg_num)
        except Exception as e:
            http_failures.append((tg_num, None, None, 'page_error', str(e)))
            continue
        for tid in repl_ids:
            if tid not in task_by_id:
                continue
            link, task = task_by_id[tid]
            parsed = parse_replacements_lines_text(
                task.text, (task.checker_data or '').strip() or None
            )
            for li in range(len(parsed['right_tokens'])):
                answers = task_replacements_canonical_answer_row(task, li)
                if not answers:
                    continue
                http_checked += 1
                code, body = http_submit_line(
                    session, tid, game_id, li, answers, csrf, anon_key
                )
                ok = (
                    code == 200
                    and body.get('status') == 'ok'
                    and body.get('attempt_status') in (None, 'Ok', 'Partial')
                )
                # attempt_status may be in update_html
                if not ok and body.get('status') == 'ok':
                    ast = body.get('attempt_status') or body.get('status_attempt')
                    if ast is None:
                        html_frag = json.dumps(body)[:200]
                        if 'replacements-row--solved' in html_frag or '"Ok"' in html_frag:
                            ok = True
                if code == 200 and body.get('status') == 'ok':
                    # inspect embedded status in response
                    resp_s = json.dumps(body, ensure_ascii=False)
                    if '"attempt_status": "Wrong"' in resp_s or "'Wrong'" in resp_s:
                        if 'line_done' not in resp_s:
                            ok = False
                if not ok:
                    http_failures.append(
                        (link.number, tid, li, f'http_{code}', body)
                    )

    print(f'HTTP anon: {http_checked} lines, {len(http_failures)} failures')
    for row in http_failures[:20]:
        print(' HTTP_FAIL', row[0], row[1], 'L' + str(row[2]), row[3], str(row[4])[:120])

    return 1 if (failures or http_failures) else 0


if __name__ == '__main__':
    sys.exit(main())
