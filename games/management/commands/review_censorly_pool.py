"""Tinder-style keep/drop review for Censorly article pool.

Modes:
  pool     — whole article_pool.txt minus already decided titles (default)
  obscure  — obscure_drop_candidates / report only

Usage:
  ../venv/interoves_django/bin/python manage.py review_censorly_pool
  ../venv/interoves_django/bin/python manage.py review_censorly_pool obscure
  # http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand

from games.censorly.pool_build import ARTICLE_POOL_ALLOW_PATH, ARTICLE_POOL_DENY_PATH

VAR = Path('var/censorly_pool')
REPORT = VAR / 'obscure_drop_report.json'
CANDIDATES_TXT = VAR / 'obscure_drop_candidates.txt'
DECISIONS = VAR / 'pool_review_decisions.json'
LEGACY_DECISIONS = VAR / 'obscure_review_decisions.json'
ACCEPTED = VAR / 'accepted.json'
POOL = Path('games/censorly/article_pool.txt')
DRAFT = VAR / 'pool_draft.txt'
UI = Path('games/censorly/pool_build/review_ui.html')


def _load_accepted_by_title() -> dict[str, dict]:
    if not ACCEPTED.is_file():
        return {}
    try:
        rows = json.loads(ACCEPTED.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    return {r['title']: r for r in rows if isinstance(r, dict) and r.get('title')}


def _meta_for(title: str, accepted: dict[str, dict], extra: dict | None = None) -> dict:
    a = accepted.get(title) or {}
    base = {
        'title': title,
        'reasons': list((extra or {}).get('reasons') or []),
        'sitelinks': (extra or {}).get('sitelinks', a.get('langlinks')),
        'sources': list((extra or {}).get('sources') or a.get('sources') or []),
        'themes': list(a.get('themes') or []),
        'pageviews': a.get('pageviews'),
        'length': a.get('length'),
    }
    return base


def _load_obscure_candidates(accepted: dict[str, dict]) -> list[dict]:
    if REPORT.is_file():
        data = json.loads(REPORT.read_text(encoding='utf-8'))
        if isinstance(data, list) and data:
            return [_meta_for(r['title'], accepted, r) for r in data if r.get('title')]
    if not CANDIDATES_TXT.is_file():
        return []
    titles = [
        line.strip()
        for line in CANDIDATES_TXT.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    return [_meta_for(t, accepted) for t in titles]


def _load_pool_candidates(accepted: dict[str, dict]) -> list[dict]:
    if not POOL.is_file():
        return []
    titles = [
        line.strip()
        for line in POOL.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    return [_meta_for(t, accepted) for t in titles]


def _load_decisions() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (LEGACY_DECISIONS, DECISIONS):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            for k, v in raw.items():
                if v in ('keep', 'drop'):
                    merged[k] = v
    return merged


def _save_decisions(decisions: dict[str, str]) -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(decisions, ensure_ascii=False, indent=2) + '\n'
    DECISIONS.write_text(payload, encoding='utf-8')
    # keep legacy file in sync so older paths still see progress
    LEGACY_DECISIONS.write_text(payload, encoding='utf-8')


def _write_title_list(path: Path, titles: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(titles) + ('\n' if titles else ''), encoding='utf-8')


def _export_review_lists(decisions: dict[str, str], remaining: list[str]) -> dict[str, str]:
    """Persist keep/drop/remaining for future harvests."""
    keep = sorted(t for t, v in decisions.items() if v == 'keep')
    drop = sorted(t for t, v in decisions.items() if v == 'drop')
    paths = {
        'keep': VAR / 'review_keep.txt',
        'drop': VAR / 'review_drop.txt',
        'remaining': VAR / 'review_remaining.txt',
        'allow': ARTICLE_POOL_ALLOW_PATH,
        'deny': ARTICLE_POOL_DENY_PATH,
        'manifest': VAR / 'review_lists.json',
    }
    _write_title_list(paths['keep'], keep)
    _write_title_list(paths['drop'], drop)
    _write_title_list(paths['remaining'], remaining)
    _write_title_list(paths['allow'], remaining)
    _write_title_list(paths['deny'], drop)
    paths['manifest'].write_text(
        json.dumps(
            {
                'keep_count': len(keep),
                'drop_count': len(drop),
                'remaining_count': len(remaining),
                'files': {k: str(v) for k, v in paths.items()},
                'note': (
                    'deny = never re-add on future filter; '
                    'allow/remaining = curated pool snapshot after review'
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )
    return {k: str(v) for k, v in paths.items()}


def _apply_to_pool(decisions: dict[str, str]) -> dict:
    drop = {t for t, v in decisions.items() if v == 'drop'}
    keep = {t for t, v in decisions.items() if v == 'keep'}
    if not POOL.is_file():
        raise FileNotFoundError(POOL)
    titles = [line.strip() for line in POOL.read_text(encoding='utf-8').splitlines() if line.strip()]
    before = len(titles)
    remaining = [t for t in titles if t not in drop]
    removed = before - len(remaining)
    text = '\n'.join(remaining) + ('\n' if remaining else '')
    POOL.write_text(text, encoding='utf-8')
    if DRAFT.is_file():
        DRAFT.write_text(text, encoding='utf-8')
    exported = _export_review_lists(decisions, remaining)
    try:
        from games.censorly.article_pool import load_article_pool

        load_article_pool.cache_clear()
    except Exception:
        pass
    return {
        'removed': removed,
        'kept': len(keep),
        'pool_size': len(remaining),
        'drop_marked': len(drop),
        'lists': exported,
    }


class Handler(BaseHTTPRequestHandler):
    mode: str = 'pool'
    candidates: list[dict] = []
    decisions: dict[str, str] = {}

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[0]).startswith(('GET /api/', 'POST /api/decide')):
            return
        super().log_message(fmt, *args)

    def _json(self, code: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            data = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            html = UI.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(html)
            return
        if path == '/api/state':
            # Only send decisions that matter for this candidate set, but keep full
            # file on disk. UI skips titles present in decisions.
            cand_titles = {c['title'] for c in self.candidates}
            visible_decisions = {
                t: v for t, v in self.decisions.items() if t in cand_titles
            }
            self._json(
                200,
                {
                    'mode': self.mode,
                    'candidates': self.candidates,
                    'decisions': visible_decisions,
                    'skipped_already': sum(1 for t in self.decisions if t in cand_titles),
                },
            )
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        data = self._read_json()
        if path == '/api/decide':
            title = (data.get('title') or '').strip()
            verdict = data.get('verdict')
            if not title or verdict not in ('keep', 'drop'):
                self._json(400, {'error': 'bad request'})
                return
            self.decisions[title] = verdict
            _save_decisions(self.decisions)
            self._json(200, {'ok': True, 'decisions': len(self.decisions)})
            return
        if path == '/api/undo':
            title = (data.get('title') or '').strip()
            verdict = data.get('verdict')
            if not title:
                self._json(400, {'error': 'bad request'})
                return
            if verdict in ('keep', 'drop'):
                self.decisions[title] = verdict
            else:
                self.decisions.pop(title, None)
            _save_decisions(self.decisions)
            self._json(200, {'ok': True})
            return
        if path == '/api/apply':
            try:
                result = _apply_to_pool(self.decisions)
            except Exception as exc:
                self._json(500, {'error': str(exc)})
                return
            self._json(200, result)
            return
        self._json(404, {'error': 'not found'})


class Command(BaseCommand):
    help = 'Tinder-style review UI for article pool / obscure candidates'

    def add_arguments(self, parser):
        parser.add_argument(
            'mode',
            nargs='?',
            default='pool',
            choices=['pool', 'obscure'],
            help='pool = whole article_pool minus decided; obscure = drop-candidate list',
        )
        parser.add_argument('--host', default='127.0.0.1')
        parser.add_argument('--port', type=int, default=8765)
        parser.add_argument('--no-browser', action='store_true')

    def handle(self, *args, **options):
        if not UI.is_file():
            self.stderr.write(f'Missing UI file: {UI}')
            return

        mode = options['mode']
        accepted = _load_accepted_by_title()
        decisions = _load_decisions()
        # persist merged legacy → new on start
        _save_decisions(decisions)

        if mode == 'obscure':
            candidates = _load_obscure_candidates(accepted)
            if not candidates:
                self.stderr.write('No obscure candidates found')
                return
        else:
            candidates = _load_pool_candidates(accepted)
            if not candidates:
                self.stderr.write(f'Empty pool: {POOL}')
                return

        undecided = sum(1 for c in candidates if c['title'] not in decisions)
        Handler.mode = mode
        Handler.candidates = candidates
        Handler.decisions = decisions

        host = options['host']
        port = options['port']
        url = f'http://{host}:{port}/'
        self.stdout.write(f'Mode: {mode}')
        self.stdout.write(f'Candidates: {len(candidates)} · undecided: {undecided}')
        self.stdout.write(f'Decisions on disk: {len(decisions)}')
        self.stdout.write(f'Open {url}')
        self.stdout.write(f'← drop · → keep · {DECISIONS}')

        httpd = ThreadingHTTPServer((host, port), Handler)
        if not options['no_browser']:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            self.stdout.write('\nStopped.')
        finally:
            httpd.server_close()
