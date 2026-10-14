"""Find article-local word pairs that the Censorly matcher opens.

This is a read-only diagnostic. It scans distinct article surfaces through the
same inverted index and relation function used by gameplay, then reports each
opening direction and the reason the pair opens.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.lexical.core import fold
from games.censorly.flags import open_pronouns_enabled
from games.censorly.lexical.match import _ArticleIndex, _GuessPack, _relation
from games.censorly.lexical.rootbank import structures_of, tikhonov_roots_of
from games.censorly.tokenize import all_tokens, build_puzzle_payload


FIELDS = (
    'article', 'word_a', 'word_b', 'lemma_a', 'lemma_b', 'opens_a_to_b',
    'reason_a_to_b', 'opens_b_to_a', 'reason_b_to_a', 'surfaces_a',
    'surfaces_b', 'count_a', 'count_b', 'tikhonov_roots',
    'structures_a', 'structures_b',
)


def _article_payload(args):
    sources = sum(bool(value) for value in (
        args.text_file, args.payload_file, args.article_title, args.task_number,
    ))
    if sources != 1:
        raise ValueError('exactly one of --text-file, --payload-file, --article-title is required')
    if args.text_file:
        return build_puzzle_payload(
            wiki_title=args.article or args.text_file.stem,
            body_text=args.text_file.read_text(encoding='utf-8'),
        )
    if args.payload_file:
        from games.censorly.tokenize import resolve_puzzle_payload
        return resolve_puzzle_payload(json.loads(args.payload_file.read_text(encoding='utf-8')))
    if args.task_number:
        from games.censorly import CENSORLY_GAME_ID, CENSORLY_TAGS_KEY
        from games.censorly.play import get_task_for_number
        from games.models import Game
        _link, task = get_task_for_number(Game.objects.get(pk=CENSORLY_GAME_ID), args.task_number)
        payload = (task.tags or {}).get(CENSORLY_TAGS_KEY)
        if not isinstance(payload, dict):
            raise ValueError(f'Censorly task {args.task_number!r} has no saved puzzle payload')
        return payload
    from games.censorly.wiki import fetch_article
    article = fetch_article(args.article_title)
    return build_puzzle_payload(
        wiki_title=article.title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        wiki_revid=article.revid,
    )


def _article_surfaces(payload):
    """Map normalized surfaces to stored lemmas and occurrence counts."""
    rows = Counter()
    lemmas = {}
    for token in all_tokens(payload):
        if token.get('kind') != 'content':
            continue
        surface = fold(token.get('surface') or '')
        lemma = fold(token.get('lemma') or '')
        if surface and lemma:
            rows[(surface, lemma)] += 1
            lemmas.setdefault(surface, set()).add(lemma)
    return rows, lemmas


def find_open_pairs(payload, *, include_open=False, root_only=True):
    rows, lemmas_by_surface = _article_surfaces(payload)
    lemma_surfaces = {}
    lemma_counts = Counter()
    for (surface, lemma), count in rows.items():
        lemma_counts[lemma] += count
    for surface, lemmas in lemmas_by_surface.items():
        for lemma in lemmas:
            lemma_surfaces.setdefault(lemma, set()).add(surface)
    include_pronouns = open_pronouns_enabled()
    surfaces = tuple(sorted(lemmas_by_surface))
    target_contexts = tuple(sorted(
        (surface, next(iter(lemmas)))
        for surface, lemmas in lemmas_by_surface.items()
        if len(lemmas) == 1
    ))
    targets = _ArticleIndex(
        surfaces,
        include_pronouns=include_pronouns,
        target_contexts=target_contexts,
    )
    guesses = {
        surface: _GuessPack(
            surface,
            include_pronouns=include_pronouns,
            article_lemma=next(iter(lemmas_by_surface[surface]))
            if len(lemmas_by_surface[surface]) == 1 else None,
        )
        for surface in surfaces
    }
    lemma_pairs = {}
    for word_a in surfaces:
        for word_b in targets.candidates(guesses[word_a]):
            if word_b <= word_a:
                continue
            reason_ab = _relation(guesses[word_a], targets.by_surface[word_b])
            reason_ba = _relation(guesses[word_b], targets.by_surface[word_a])
            if root_only and (
                'root' not in (reason_ab, reason_ba)
                or 'lexeme' in (reason_ab, reason_ba)
            ):
                continue
            if not reason_ab and not reason_ba:
                continue
            if reason_ab and reason_ba and not include_open:
                continue
            for lemma_a in sorted(lemmas_by_surface[word_a]):
                for lemma_b in sorted(lemmas_by_surface[word_b]):
                    if lemma_a == lemma_b:
                        continue
                    pair_a, pair_b = sorted((lemma_a, lemma_b))
                    key = (pair_a, pair_b)
                    pair = lemma_pairs.setdefault(key, {
                        'article': payload.get('wiki_title') or '',
                        'lemma_a': pair_a,
                        'lemma_b': pair_b,
                        'surfaces_a': set(),
                        'surfaces_b': set(),
                        'reasons_a_to_b': set(),
                        'reasons_b_to_a': set(),
                    })
                    if lemma_a == pair_a:
                        pair['surfaces_a'].add(word_a)
                        pair['surfaces_b'].add(word_b)
                        if reason_ab:
                            pair['reasons_a_to_b'].add(reason_ab)
                        if reason_ba:
                            pair['reasons_b_to_a'].add(reason_ba)
                    else:
                        pair['surfaces_a'].add(word_b)
                        pair['surfaces_b'].add(word_a)
                        if reason_ab:
                            pair['reasons_b_to_a'].add(reason_ab)
                        if reason_ba:
                            pair['reasons_a_to_b'].add(reason_ba)

    # Near-miss candidates: equal complete Tikhonov ROOT spelling is an
    # independent dictionary signal even when both packs are unresolved and
    # therefore absent from the gameplay inverted index.
    by_roots = {}
    for lemma in (() if root_only else lemma_surfaces):
        roots = tuple(sorted(fold(root) for root in tikhonov_roots_of(lemma) if root))
        if roots:
            by_roots.setdefault(roots, set()).add(lemma)
    for roots, members in by_roots.items():
        members = sorted(members)
        for index, lemma_a in enumerate(members):
            for lemma_b in members[index + 1:]:
                key = (lemma_a, lemma_b)
                if key in lemma_pairs:
                    continue
                word_a = sorted(lemma_surfaces[lemma_a])[0]
                word_b = sorted(lemma_surfaces[lemma_b])[0]
                reason_ab = _relation(guesses[word_a], targets.by_surface[word_b])
                reason_ba = _relation(guesses[word_b], targets.by_surface[word_a])
                if root_only and (
                    'root' not in (reason_ab, reason_ba)
                    or 'lexeme' in (reason_ab, reason_ba)
                ):
                    continue
                if not reason_ab and not reason_ba:
                    continue
                if reason_ab and reason_ba:
                    if not include_open:
                        continue
                pair = lemma_pairs.setdefault(key, {
                    'article': payload.get('wiki_title') or '',
                    'lemma_a': lemma_a,
                    'lemma_b': lemma_b,
                    'surfaces_a': set(lemma_surfaces[lemma_a]),
                    'surfaces_b': set(lemma_surfaces[lemma_b]),
                    'reasons_a_to_b': set(),
                    'reasons_b_to_a': set(),
                })
                if reason_ab:
                    pair['reasons_a_to_b'].add(reason_ab)
                if reason_ba:
                    pair['reasons_b_to_a'].add(reason_ba)

    results = []
    for pair in lemma_pairs.values():
        reason_ab = sorted(pair.pop('reasons_a_to_b'))
        reason_ba = sorted(pair.pop('reasons_b_to_a'))
        pair['surfaces_a'] = '|'.join(sorted(pair['surfaces_a']))
        pair['surfaces_b'] = '|'.join(sorted(pair['surfaces_b']))
        pair['word_a'] = pair['surfaces_a'].split('|')[0]
        pair['word_b'] = pair['surfaces_b'].split('|')[0]
        pair['opens_a_to_b'] = 'yes' if reason_ab else 'no'
        pair['reason_a_to_b'] = '|'.join(reason_ab) if reason_ab else 'CLOSED'
        pair['opens_b_to_a'] = 'yes' if reason_ba else 'no'
        pair['reason_b_to_a'] = '|'.join(reason_ba) if reason_ba else 'CLOSED'
        pair['count_a'] = str(lemma_counts[pair['lemma_a']])
        pair['count_b'] = str(lemma_counts[pair['lemma_b']])
        pair['tikhonov_roots'] = '|'.join(sorted(
            set(tikhonov_roots_of(pair['lemma_a'])) & set(tikhonov_roots_of(pair['lemma_b']))
        ))
        pair['structures_a'] = json.dumps(structures_of(pair['lemma_a']), ensure_ascii=False, sort_keys=True)
        pair['structures_b'] = json.dumps(structures_of(pair['lemma_b']), ensure_ascii=False, sort_keys=True)
        results.append(pair)
    results.sort(key=lambda row: (row['lemma_a'], row['lemma_b']))
    mutual = sum(row['opens_a_to_b'] == row['opens_b_to_a'] == 'yes' for row in results)
    one_way = sum(row['opens_a_to_b'] != row['opens_b_to_a'] for row in results)
    stats = Counter(
        pairs=len(results), mutual=mutual, one_way=one_way,
        families=len({row['tikhonov_roots'] for row in results if row['tikhonov_roots']}),
    )
    return results, len(lemmas_by_surface), stats


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description='Find article word pairs opened by shared roots rather than shared lemmas.'
    )
    parser.add_argument('--text-file', type=Path)
    parser.add_argument('--payload-file', type=Path)
    parser.add_argument('--article-title', help='fetch the current Russian Wikipedia extract')
    parser.add_argument('--task-number', help='load a saved daily Censorly task, e.g. 1')
    parser.add_argument('--article', help='label used in reports for --text-file')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--include-open', action='store_true', help='also show pairs already OPEN')
    parser.add_argument(
        '--all-reasons', action='store_true',
        help='include lexeme, grammar, alias, and dictionary near-miss candidates',
    )
    args = parser.parse_args()
    try:
        payload = _article_payload(args)
    except (LookupError, OSError, ValueError) as exc:
        parser.error(str(exc))
    root_only = not args.all_reasons
    pairs, unique_surfaces, stats = find_open_pairs(
        payload, include_open=args.include_open, root_only=root_only,
    )
    print(f"article: {payload.get('wiki_title') or args.article or 'article'}")
    print(f"wiki revision: {payload.get('wiki_revid') or 'unknown'}")
    print(f'unique content surfaces: {unique_surfaces}')
    mode = 'root-only' if root_only else 'all reasons'
    print(f"candidate pairs ({mode}; at least one opening direction): {stats['pairs']}")
    print(f"mutual: {stats['mutual']}; one-way: {stats['one_way']}")
    print(f"candidate families: {stats['families']}")
    print('word_a\tword_b\tlemma_a\tlemma_b\ta_to_b\tb_to_a\ttikhonov_roots')
    for row in pairs:
        print('\t'.join(row[key] for key in (
            'word_a', 'word_b', 'lemma_a', 'lemma_b',
            'reason_a_to_b', 'reason_b_to_a', 'tikhonov_roots',
        )))
    if args.output:
        _write(args.output, pairs)
        print(f'report: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
