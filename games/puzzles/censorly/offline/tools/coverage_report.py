"""Censorly root-coverage snapshot for the dictionary roadmap.

Advisory only: it reads the live root bank and the fixture corpus and never
edits data. Run it before and after a roadmap batch and paste the summary
line into ``docs/censorly-dictionary-roadmap.md``.

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.coverage_report \
        --gaps /tmp/censorly-gaps.tsv
"""
from __future__ import annotations

import argparse
import csv
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.rootbank import provenance
from games.puzzles.censorly.lexical.semantics import initially_open
from games.puzzles.censorly.offline.audit.prelaunch import iter_fixtures
from games.puzzles.censorly.text.tokenize import tokenize_text
from games.puzzles.core.norm_matcher import MORPH_ANALYZER

_PROPER = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'})
_GAP_SOURCES = frozenset({'ABSENT', 'UNRESOLVED', 'UNRESOLVED_TOO_AMBIGUOUS'})


def _gap_kind(lemma: str, detail: str) -> str:
    """Which roadmap track would close this gap."""
    if not re.search('[а-яё]', lemma):
        return 'non_cyrillic'
    if not MORPH_ANALYZER.word_is_known(lemma):
        return 'unknown_word'
    if _PROPER & set(MORPH_ANALYZER.parse(lemma)[0].tag.grammemes):
        return 'proper'
    if detail.startswith('morph ') and ' matches 0 ' in detail:
        return 'opaque_single'
    if detail.startswith('morph '):
        return 'ambiguous_spelling'
    if detail:
        return 'multi_component'
    return 'absent_common'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--gaps', type=Path, help='write corpus gaps as TSV')
    parser.add_argument('--top', type=int, default=15)
    args = parser.parse_args()

    prov = provenance()
    bank_sources = Counter(item['source'] for item in prov.values())

    tokens = Counter()
    gaps: dict[tuple[str, str], Counter] = defaultdict(Counter)
    details: dict[str, str] = {}
    articles = 0
    for _name, _kind, body in iter_fixtures():
        articles += 1
        for token in tokenize_text(body):
            if token.get('kind') != 'content':
                continue
            surface = token.get('surface') or ''
            if initially_open(surface):
                tokens['service'] += 1
                continue
            lemma = fold(token.get('lemma') or surface)
            info = prov.get(lemma) or {'source': 'ABSENT', 'detail': ''}
            source = info['source']
            tokens[source] += 1
            if source in _GAP_SOURCES:
                details[lemma] = info.get('detail', '')
                gaps[(source, _gap_kind(lemma, details[lemma]))][lemma] += 1

    total = sum(tokens.values()) or 1
    rooted = total - tokens['service'] - sum(
        count for source, count in tokens.items() if source in _GAP_SOURCES
    )
    content = total - tokens['service']
    print(f'bank_lemmas={len(prov)} articles={articles} content_tokens={content}')
    print('bank sources:')
    for source, count in bank_sources.most_common():
        print(f'  {count:7d} {source}')
    print('corpus tokens by source:')
    for source, count in tokens.most_common():
        print(f'  {count:7d} {100 * count / total:5.1f}% {source}')
    print('corpus gaps by track:')
    for key, lemmas in sorted(gaps.items(), key=lambda item: -sum(item[1].values())):
        top = ', '.join(lemma for lemma, _count in lemmas.most_common(args.top))
        print(f'  {sum(lemmas.values()):6d} {key[0]}/{key[1]}: {top}')
    print(
        f'SUMMARY rooted_content_tokens={100 * rooted / max(content, 1):.1f}% '
        f'unresolved_bank_lemmas={bank_sources["UNRESOLVED"]} '
        f'unique_spelling_fallback={bank_sources["UNIQUE_SPELLING_FALLBACK"]} '
        f'reviewed_opaque={bank_sources["REVIEWED_OPAQUE_ROOT"]}'
    )

    if args.gaps:
        with args.gaps.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t')
            writer.writerow(['lemma', 'corpus_count', 'source', 'track', 'detail'])
            rows = [
                (lemma, count, source, kind, details.get(lemma, ''))
                for (source, kind), lemmas in gaps.items()
                for lemma, count in lemmas.items()
            ]
            for row in sorted(rows, key=lambda row: (-row[1], row[0])):
                writer.writerow(row)
        print(f'gaps written to {args.gaps}')


if __name__ == '__main__':
    main()
