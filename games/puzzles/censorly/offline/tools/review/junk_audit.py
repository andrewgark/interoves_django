"""Wiki service marks and parser leftovers in article texts (``article.junk``).

Counts every ``JUNK_PATTERNS`` hit with its context, so a parser change can
be checked before the stored puzzles are re-parsed:

    python -m games.puzzles.censorly.offline.tools.review.junk_audit
    python -m games.puzzles.censorly.offline.tools.review.junk_audit --corpus --out /tmp/junk.tsv

The default source is the fixture articles. ``--corpus`` reads the cached
texts of ``corpus.build_corpus`` (``var/censorly_corpus/articles``), which
hold the extract as it was parsed when cached; ``--refetch`` parses those
titles again with the current parser (one Wikipedia request each), optionally
only the articles listed in an earlier ``--out`` TSV:

    python -m games.puzzles.censorly.offline.tools.review.junk_audit --corpus --out /tmp/before.tsv
    python -m games.puzzles.censorly.offline.tools.review.junk_audit --corpus \\
        --refetch /tmp/before.tsv --out /tmp/after.tsv

The played articles come from
the production database through the RDS tunnel (read-only):

    ./scripts/with_rds.sh --raw ../venv/interoves_django/bin/python \\
        -m games.puzzles.censorly.offline.tools.review.junk_audit --db

Exits with status 1 when anything is found and ``--strict`` is given.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parents[6]
CORPUS_DIR = REPO_ROOT / 'var' / 'censorly_corpus' / 'articles'


def _fixture_articles():
    from games.puzzles.censorly.offline.fixtures import iter_fixtures

    for name, _kind, body in iter_fixtures():
        yield name, body


def _corpus_articles():
    for path in sorted(CORPUS_DIR.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('body'):
            yield data.get('resolved_title') or data.get('title') or path.stem, data['body']


def _refetched(articles, only_from: str | None):
    import time

    from games.puzzles.censorly.article.wiki import WikiFetchError, fetch_article

    wanted = None
    if only_from:
        with open(only_from, encoding='utf-8') as rows:
            wanted = {line.split('\t', 1)[0] for line in rows if line.strip()}
    for name, _body in articles:
        if wanted is not None and name not in wanted:
            continue
        try:
            yield name, fetch_article(name).extract
        except WikiFetchError as exc:
            print(f'SKIP {name}: {exc}', file=sys.stderr)
        time.sleep(0.2)


def _db_articles():
    from games.models import Task
    from games.puzzles.censorly.ids import CENSORLY_TASK_TYPE
    from games.puzzles.censorly.play import _stored_puzzle_payload

    tasks = Task.objects.filter(task_type=CENSORLY_TASK_TYPE, is_removed=False).order_by('id')
    for task in tasks.iterator():
        payload, _where = _stored_puzzle_payload(task)
        if payload and isinstance(payload.get('body_text'), str):
            yield f'#{task.pk} {payload.get("wiki_title") or ""}', payload['body_text']


def audit(articles):
    """Rows (article, pattern, context) and hit counts by pattern."""
    from games.puzzles.censorly.article.junk import find_junk

    rows = []
    counts: collections.Counter = collections.Counter()
    for name, body in articles:
        for hit in find_junk(body):
            counts[hit.pattern] += 1
            rows.append((name, hit.pattern, hit.context))
    return rows, counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--corpus', action='store_true', help='cached corpus texts in var/')
    source.add_argument('--db', action='store_true', help='stored puzzles (run through with_rds.sh)')
    parser.add_argument(
        '--refetch', nargs='?', const='', default=None, metavar='TSV',
        help='with --corpus: parse the titles again (only those in TSV, when given)',
    )
    parser.add_argument('--out', help='write all hits as TSV here')
    parser.add_argument('--examples', type=int, default=3, help='examples per pattern on stdout')
    parser.add_argument('--strict', action='store_true', help='exit 1 when anything is found')
    args = parser.parse_args(argv)

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()

    articles = _db_articles() if args.db else _corpus_articles() if args.corpus else _fixture_articles()
    if args.refetch is not None:
        articles = _refetched(articles, args.refetch or None)
    rows, counts = audit(articles)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as out:
            out.write('article\tpattern\tcontext\n')
            for row in rows:
                out.write('\t'.join(cell.replace('\t', ' ') for cell in row) + '\n')
    shown: collections.Counter = collections.Counter()
    for name, pattern, context in rows:
        if shown[pattern] < args.examples:
            shown[pattern] += 1
            print(f'{pattern}\t{name}\t{context!r}')
    for pattern, count in counts.most_common():
        print(f'TOTAL {pattern}={count}')
    print(f'SUMMARY hits={sum(counts.values())} articles={len({row[0] for row in rows})}')
    return 1 if args.strict and rows else 0


if __name__ == '__main__':
    sys.exit(main())
