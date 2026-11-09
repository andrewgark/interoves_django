"""The fixture articles under ``games/tests/censorly_testdata``.

Offline tools use them as a small, fixed corpus: snapshots, profiles and
the fallback corpus when the pool corpus file is missing.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / 'tests' / 'censorly_testdata'


def iter_fixtures():
    """(relative path, kind, body) of every fixture text, in path order."""
    from games.puzzles.censorly.article.wiki import _trim_extract

    for path in sorted(ROOT.rglob('*.txt')):
        rel = path.relative_to(ROOT).as_posix()
        if path.parent.name == 'articles':
            kind = 'article'
            body = path.read_text(encoding='utf-8')
        elif path.parent.name == 'leads':
            kind = 'lead'
            body = path.read_text(encoding='utf-8')
        else:
            kind = 'raw' if path.parent == ROOT else 'pool'
            body, _truncated = _trim_extract(path.read_text(encoding='utf-8'))
        yield rel, kind, body


def content_surfaces(body: str) -> list[str]:
    from games.puzzles.censorly.text.tokenize import build_puzzle_payload

    payload = build_puzzle_payload(wiki_title='', body_text=body)
    return [
        tok.get('surface') or ''
        for tok in payload['body_tokens']
        if tok.get('kind') == 'content' and (tok.get('surface') or '')
    ]
