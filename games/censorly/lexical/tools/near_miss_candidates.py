"""Find article-local CLOSED pairs with independent similarity evidence.

Advisory only: this tool never changes reviewed data or resolver semantics.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.flags import open_pronouns_enabled
from games.censorly.lexical.core import fold
from games.censorly.lexical.match import _ArticleIndex, _GuessPack, _Pack, _relation
from games.censorly.lexical.rootbank import structures_of, tikhonov_roots_of
from games.censorly.tokenize import all_tokens, resolve_puzzle_payload


FIELDS = (
    'rank', 'article', 'lemma_a', 'lemma_b', 'surface_a', 'surface_b',
    'actual', 'evidence', 'similarity', 'shared_tikhonov_roots',
    'structures_a', 'structures_b', 'count_a', 'count_b',
)


def _lemma_data(payload):
    surfaces_by_lemma = {}
    counts = Counter()
    lemmas_by_surface = {}
    for token in all_tokens(payload):
        if token.get('kind') != 'content':
            continue
        surface = fold(token.get('surface') or '')
        lemma = fold(token.get('lemma') or '')
        if not surface or not lemma:
            continue
        surfaces_by_lemma.setdefault(lemma, set()).add(surface)
        counts[lemma] += 1
        lemmas_by_surface.setdefault(surface, set()).add(lemma)
    return surfaces_by_lemma, counts, lemmas_by_surface


def _similarity(a, b):
    if min(len(a), len(b)) < 4:
        return 0.0
    prefix = 0
    for left, right in zip(a, b):
        if left != right:
            break
        prefix += 1
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if prefix < 4 or ratio < 0.62:
        return 0.0
    return ratio


def find_near_misses(payload, *, limit=200):
    surfaces_by_lemma, counts, lemmas_by_surface = _lemma_data(payload)
    surfaces = tuple(sorted(lemmas_by_surface))
    include_pronouns = open_pronouns_enabled()
    contexts = tuple(sorted(
        (surface, next(iter(lemmas)))
        for surface, lemmas in lemmas_by_surface.items()
        if len(lemmas) == 1
    ))
    targets = _ArticleIndex(
        surfaces, include_pronouns=include_pronouns,
        target_contexts=contexts,
    )
    packs = {
        surface: _GuessPack(
            surface, include_pronouns=include_pronouns,
            article_lemma=next(iter(lemmas_by_surface[surface]))
            if len(lemmas_by_surface[surface]) == 1 else None,
        )
        for surface in surfaces
    }
    lemmas = sorted(surfaces_by_lemma)
    roots = {
        lemma: frozenset(fold(root) for root in tikhonov_roots_of(lemma) if root)
        for lemma in lemmas
    }
    candidates = []
    for index, lemma_a in enumerate(lemmas):
        for lemma_b in lemmas[index + 1:]:
            shared = sorted(roots[lemma_a] & roots[lemma_b])
            similarity = _similarity(lemma_a, lemma_b)
            evidence = []
            if shared:
                evidence.append('SHARED_TIKHONOV_ROOT')
            if similarity:
                evidence.append('MORPHOLOGICAL_SURFACE_SIMILARITY')
            if not evidence:
                continue
            surface_a = sorted(surfaces_by_lemma[lemma_a])[0]
            surface_b = sorted(surfaces_by_lemma[lemma_b])[0]
            reason_ab = _relation(packs[surface_a], targets.by_surface[surface_b])
            reason_ba = _relation(packs[surface_b], targets.by_surface[surface_a])
            if reason_ab or reason_ba:
                continue
            candidates.append({
                'article': payload.get('wiki_title') or '',
                'lemma_a': lemma_a,
                'lemma_b': lemma_b,
                'surface_a': surface_a,
                'surface_b': surface_b,
                'actual': 'CLOSED',
                'evidence': '|'.join(evidence),
                'similarity': f'{similarity:.3f}',
                'shared_tikhonov_roots': '|'.join(shared),
                'structures_a': json.dumps(structures_of(lemma_a), ensure_ascii=False),
                'structures_b': json.dumps(structures_of(lemma_b), ensure_ascii=False),
                'count_a': str(counts[lemma_a]),
                'count_b': str(counts[lemma_b]),
                '_score': (bool(shared) * 2, similarity, counts[lemma_a] + counts[lemma_b]),
            })
    candidates.sort(key=lambda row: row['_score'], reverse=True)
    for rank, row in enumerate(candidates[:limit], 1):
        row['rank'] = str(rank)
        del row['_score']
    return candidates[:limit], len(surfaces), len(lemmas)


def main():
    parser = argparse.ArgumentParser(description='Find CLOSED near-miss pairs in a Censorly payload.')
    parser.add_argument('--payload-file', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    payload = resolve_puzzle_payload(json.loads(args.payload_file.read_text(encoding='utf-8')))
    rows, surface_count, lemma_count = find_near_misses(payload, limit=args.limit)
    print(f"article: {payload.get('wiki_title') or 'article'}")
    print(f'unique content surfaces: {surface_count}')
    print(f'unique lemmas: {lemma_count}')
    print(f'CLOSED near-miss candidates: {len(rows)}')
    print('rank\tlemma_a\tlemma_b\tsurface_a\tsurface_b\tevidence\tsimilarity\tshared_tikhonov_roots')
    for row in rows:
        print('\t'.join(row[key] for key in (
            'rank', 'lemma_a', 'lemma_b', 'surface_a', 'surface_b',
            'evidence', 'similarity', 'shared_tikhonov_roots',
        )))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        print(f'report: {args.output}')


if __name__ == '__main__':
    raise SystemExit(main())
