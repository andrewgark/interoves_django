"""Family-level review sheet for the top Censorly corpus gaps.

Advisory only. For each rootless lemma ranked by corpus frequency, print its
Tikhonov root, the whole Tikhonov family with every member's current game
structure, and the Wiktionary nest-anchor proposal, so a reviewer decides a
family (opaque root, own ``root:`` id, existing root, anchor or rejection)
rather than one pair::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.review_sheet --start 200 --count 200
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections import defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.rootbank import (
    _TIKHONOV,
    _lines,
    _plain,
    _strip_note,
    provenance,
    structures_of,
    tikhonov_roots_of,
)
from games.puzzles.censorly.lexical.semantics import _ALIASES
from games.puzzles.censorly.offline.corpus import load_corpus
from games.puzzles.censorly.offline.tools.coverage_report import _GAP_SOURCES, _gap_kind, _opens_at_runtime

TRACKS = ('opaque_single', 'absent_common', 'ambiguous_spelling', 'multi_component')
DATA = Path(__file__).resolve().parents[2] / 'lexical' / 'data'


def _short(structures) -> str:
    if not structures:
        return ''
    text = ';'.join('+'.join(item) for item in structures)
    return '=' + text.replace('fam:', '').replace('sense:', 's:')


def _nest_proposals() -> dict[str, dict]:
    from games.puzzles.censorly.offline.tools.wiktionary import nest_queue

    with tempfile.NamedTemporaryFile(suffix='.tsv') as handle:
        import sys
        argv = sys.argv
        sys.argv = ['nest_queue', '--output', handle.name, '--all']
        try:
            nest_queue.main()
        finally:
            sys.argv = argv
        with open(handle.name, encoding='utf-8', newline='') as stream:
            return {row['lemma']: row for row in csv.DictReader(stream, delimiter='\t')}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--tracks', default=','.join(TRACKS))
    parser.add_argument('--members', type=int, default=12)
    args = parser.parse_args()
    tracks = set(args.tracks.split(','))

    corpus = load_corpus()
    prov = provenance()
    family: dict[str, set[str]] = defaultdict(set)
    for line in _lines(_TIKHONOV):
        raw, seg = line.split('\t', 1)
        for part in seg.split('/'):
            if part.endswith(':ROOT'):
                family[_plain(part.rsplit(':', 1)[0])].add(fold(_strip_note(raw)))
    rejected = {
        line.split('\t', 1)[0]
        for line in (DATA / 'rejected_opaque_roots.tsv').read_text(encoding='utf-8').splitlines()[1:]
    }

    alias_words = frozenset(word for group in _ALIASES for word in group)
    gaps = []
    for lemma, count in corpus.tokens.items():
        info = prov.get(lemma) or {'source': 'ABSENT', 'detail': ''}
        if info['source'] not in _GAP_SOURCES or len(lemma) <= 2:
            continue  # letters and two-letter abbreviations are not families
        if _opens_at_runtime(lemma, corpus.surfaces.get(lemma) or lemma, alias_words):
            continue
        track = _gap_kind(lemma, info.get('detail', ''))
        if track in tracks:
            gaps.append((count, lemma, track))
    gaps.sort(key=lambda item: (-item[0], item[1]))
    window = gaps[args.start:args.start + args.count]
    nests = _nest_proposals()

    for count, lemma, track in window:
        roots = tikhonov_roots_of(lemma)
        print(f'{lemma}:{count} [{track}] tikh={"+".join(roots) or "-"}')
        for root in dict.fromkeys(roots):
            members = sorted(family[root], key=lambda item: -corpus.tokens.get(item, 0))
            flag = ' REJECTED' if root in rejected else ''
            shown = ', '.join(f'{item}{_short(structures_of(item))}' for item in members[:args.members])
            print(f'   root {root}{flag} ({len(members)}): {shown}')
        proposal = nests.get(lemma)
        if proposal:
            print(
                f'   nest→ {proposal["anchor"]}{_short(structures_of(proposal["anchor"]))} '
                f'related={proposal["related"] or "-"} alt: {proposal["alternatives"][:150]}'
            )


if __name__ == '__main__':
    main()
