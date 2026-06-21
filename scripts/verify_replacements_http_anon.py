#!/usr/bin/env python3
"""HTTP: submit canonical answers as anonymous on interoves.com."""
import json
import re
import sys
import uuid

import requests

BASE = 'https://interoves.com'
ANSWERS_PATH = '/tmp/repl_prod_answers.json'


def main():
    with open(ANSWERS_PATH, encoding='utf-8') as f:
        tasks = json.load(f)

    session = requests.Session()
    session.headers['User-Agent'] = 'interoves-verify/1.0'
    anon_key = str(uuid.uuid4())
    failures = []
    ok_count = 0

    by_tg = {}
    for item in tasks:
        by_tg.setdefault(item['tg'], []).append(item)

    for tg in sorted(by_tg):
        url = f'{BASE}/games/replacements/{tg}/'
        r = session.get(url, timeout=90)
        if r.status_code != 200:
            failures.append((tg, None, None, f'page_{r.status_code}', ''))
            continue
        html = r.text
        csrf_m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        csrf = csrf_m.group(1) if csrf_m else session.cookies.get('csrftoken', '')
        game_m = re.search(r'name="game_id" value="([^"]+)"', html)
        game_id = game_m.group(1) if game_m else 'replacements'

        for item in by_tg[tg]:
            tid = item['task_id']
            if f'repl-form-{tid}-' not in html and f'data-task-id="{tid}"' not in html:
                failures.append((tg, tid, None, 'task_not_on_page', ''))
                continue
            for li, answers in enumerate(item['lines']):
                if not answers:
                    failures.append((tg, tid, li, 'empty_answers', ''))
                    continue
                post_url = f'{BASE}/send_attempt/{tid}/'
                data = {
                    'csrfmiddlewaretoken': csrf,
                    'game_id': game_id,
                    'anon_key': anon_key,
                    'line_index': str(li),
                    'answers': json.dumps(answers, ensure_ascii=False),
                }
                resp = session.post(
                    post_url,
                    data=data,
                    headers={'Referer': url},
                    timeout=90,
                )
                ok_count += 1
                if resp.status_code != 200:
                    failures.append((tg, tid, li, f'http_{resp.status_code}', resp.text[:80]))
                    continue
                try:
                    body = resp.json()
                except Exception:
                    failures.append((tg, tid, li, 'not_json', resp.text[:80]))
                    continue
                if body.get('status') != 'ok':
                    failures.append((tg, tid, li, body.get('status'), str(body)[:120]))
                    continue
                # Wrong line: response HTML often contains attempt feedback
                blob = json.dumps(body, ensure_ascii=False)
                if '"Wrong"' in blob and 'replacements-row--solved' not in blob:
                    if f'"line_index": {li}' in blob or f'line_index\\": {li}' in blob:
                        failures.append((tg, tid, li, 'wrong_in_response', blob[:200]))

    print(f'POST {ok_count} lines, anon_key={anon_key[:8]}...')
    print(f'failures: {len(failures)}')
    for f in failures[:25]:
        print(' ', f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
