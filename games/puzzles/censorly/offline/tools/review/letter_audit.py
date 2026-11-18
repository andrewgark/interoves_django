"""Every one-letter word the tokenizer could open, with its state and context.

A one-letter word is a conjunction, preposition or particle («а», «в», «б»)
or the letter itself («А-76», «План Б», «ВКП(б)», «°С»). The tokenizer
decides which (``_close_initials`` and ``_close_letter_symbols`` in
``text/tokenize.py``). This tool lists each occurrence so a rule change can
be reviewed as a diff of two runs:

    python -m games.puzzles.censorly.offline.tools.review.letter_audit --out /tmp/before.tsv
    # change the rule
    python -m games.puzzles.censorly.offline.tools.review.letter_audit --out /tmp/after.tsv
    diff <(cut -f2- /tmp/before.tsv) <(cut -f2- /tmp/after.tsv)

The fixture articles are a small corpus. For the played articles read the
production database through the RDS tunnel (read-only):

    ./scripts/with_rds.sh --raw ../venv/interoves_django/bin/python \\
        -m games.puzzles.censorly.offline.tools.review.letter_audit --db --out /tmp/letters.tsv

Columns: article, state, surface, context. ``state`` is ``letter`` (closed,
opened by the letter alone), ``initial`` (the same, with a period: «А. П.»)
or ``open``. ``--suspicious`` keeps only the open capitals that do not
start a sentence: the likeliest misses.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

import django

LETTERS = frozenset('абвжикосуя')
_SENTENCE_END = (
    '.', '!', '?', '…', ':', '—', '–', '•', '«', '„', '»', '“', '"', '(', ')', '[', ']',
)


def _fixture_articles():
    from games.puzzles.censorly.offline.fixtures import iter_fixtures

    for name, _kind, body in iter_fixtures():
        yield name, '', body


def _db_articles():
    from games.models.catalog import GameTaskGroup
    from games.puzzles.censorly.ids import CENSORLY_GAME_ID
    from games.puzzles.censorly.play import _stored_puzzle_payload

    links = GameTaskGroup.objects.filter(game__id=CENSORLY_GAME_ID).select_related('task_group')
    for link in links.order_by('number'):
        for task in link.task_group.tasks.all():
            payload, _where = _stored_puzzle_payload(task)
            if payload and isinstance(payload.get('body_text'), str):
                yield str(link.number), payload.get('wiki_title') or '', payload['body_text']


def _context(tokens, index: int, width: int = 6) -> str:
    text = ''.join(
        tok.get('surface') or '' for tok in tokens[max(0, index - width):index + width + 1]
    )
    return ' '.join(text.split())


def _state(tokens, index: int) -> str:
    from games.puzzles.censorly.text.tokenize import content_token_open_at_start

    tok = tokens[index]
    if tok.get('kind') == 'content' and not content_token_open_at_start(tok):
        if not tok.get('initial'):
            return 'closed'
        following = tokens[index + 1].get('surface') if index + 1 < len(tokens) else ''
        return 'initial' if following in {'.', '.-'} else 'letter'
    return 'open' if tok.get('kind') in {'stop', 'content'} else ''


def _suspicious(tokens, index: int) -> bool:
    """An open capital that does not start a sentence, a line or a quote."""
    from games.puzzles.censorly.text.tokenize import _previous_nonspace

    surface = tokens[index].get('surface') or ''
    if not surface.isupper():
        return False
    previous = _previous_nonspace(tokens, index)
    if previous < 0 or tokens[previous].get('kind') == 'block':
        return False
    if bool(tokens[previous].get('in_heading')) != bool(tokens[index].get('in_heading')):
        return False
    gap = ''.join(tok.get('surface') or '' for tok in tokens[previous + 1:index])
    if '\n' in gap:
        return False
    return not (tokens[previous].get('surface') or '').endswith(_SENTENCE_END)


def audit(articles, *, suspicious_only: bool = False):
    """Rows (article, state, surface, context) and counts by (surface, state)."""
    from games.puzzles.censorly.text.tokenize import all_tokens, build_puzzle_payload

    rows = []
    counts: collections.Counter = collections.Counter()
    for name, title, body in articles:
        payload = build_puzzle_payload(wiki_title=title, body_text=body)
        tokens = all_tokens(payload)
        for index, tok in enumerate(tokens):
            surface = tok.get('surface') or ''
            if len(surface) != 1 or surface.lower() not in LETTERS:
                continue
            state = _state(tokens, index)
            if not state or state == 'closed':
                continue
            if suspicious_only and (state != 'open' or not _suspicious(tokens, index)):
                continue
            counts[surface, state] += 1
            rows.append((name, state, surface, _context(tokens, index)))
    return rows, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', action='store_true', help='read the Censorly articles from the database')
    parser.add_argument('--out', type=Path, help='write the rows as TSV here')
    parser.add_argument(
        '--suspicious', action='store_true',
        help='only open capitals that do not start a sentence',
    )
    args = parser.parse_args()

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()

    articles = _db_articles() if args.db else _fixture_articles()
    rows, counts = audit(articles, suspicious_only=args.suspicious)
    for (surface, state), count in sorted(counts.items()):
        print(f'{surface}\t{state}\t{count}')
    if args.out:
        with args.out.open('w', encoding='utf-8') as out:
            for row in rows:
                out.write('\t'.join(row) + '\n')
        print(f'{len(rows)} rows → {args.out}', file=sys.stderr)


if __name__ == '__main__':
    main()
