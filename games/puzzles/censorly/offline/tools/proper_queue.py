"""Review queue for proper names and their adjectives (roadmap track P).

Advisory only. Pairs a corpus proper noun (pymorphy Geox/Name/Surn/Orgn or a
proper gap) with corpus adjectives in -ский/-цкий and demonym nouns
(-ец, -янин, -ка) that share a long prefix with it, keeps the pairs the live
matcher does not open yet, and ranks them by corpus tokens. A reviewer adds
approved families as clusters to ``lexical/match/proper_names.py``::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.proper_queue --count 150 --tsv /tmp/proper.tsv
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical.match import matching_lemmas
from games.puzzles.censorly.offline.corpus import load_corpus
from games.puzzles.censorly.text.tokenize import tokenize_text
from games.puzzles.core.norm_matcher import MORPH_ANALYZER

_PROPER = {'Geox', 'Name', 'Surn', 'Orgn', 'Trad'}
_ADJ_ENDINGS = ('ский', 'цкий')
_DEMONYM_ENDINGS = ('ец', 'янин', 'анин', 'янка', 'анка', 'ка', 'ец')


def _stem(noun: str) -> str:
    for ending in ('ия', 'ья', 'ея', 'ь', 'й', 'а', 'я', 'о', 'е', 'ы', 'и'):
        if noun.endswith(ending) and len(noun) - len(ending) >= 3:
            return noun[: -len(ending)]
    return noun


def _is_proper(lemma: str) -> bool:
    parses = MORPH_ANALYZER.parse(lemma)
    return any(_PROPER & set(p.tag.grammemes) for p in parses[:2])


def _opens(guess: str, target: str, surface: str) -> bool:
    return bool(matching_lemmas(tokenize_text(surface or target), guess))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--count', type=int, default=150)
    parser.add_argument('--min-prefix', type=int, default=4)
    parser.add_argument('--tsv')
    args = parser.parse_args()

    corpus = load_corpus()
    lemmas = [w for w in corpus.tokens if w.isalpha() and len(w) >= 3]
    nouns = [w for w in lemmas if _is_proper(w)]
    derived = [
        w for w in lemmas
        if w.endswith(_ADJ_ENDINGS) or (w.endswith(_DEMONYM_ENDINGS) and len(w) >= 6)
    ]
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for word in derived:
        by_prefix[word[: args.min_prefix]].append(word)

    families: dict[str, list[str]] = {}
    for noun in nouns:
        stem = _stem(noun)
        if len(stem) < args.min_prefix:
            continue
        mates = [
            w for w in by_prefix.get(stem[: args.min_prefix], ())
            if w != noun and w.startswith(stem) and len(w) - len(stem) <= 6
        ]
        if not mates:
            continue
        closed = [
            w for w in mates
            if not _opens(noun, w, corpus.surfaces.get(w)) and not _opens(w, noun, corpus.surfaces.get(noun))
        ]
        if closed:
            families[noun] = closed

    ranked = sorted(
        families.items(),
        key=lambda item: -(corpus.tokens.get(item[0], 0) + sum(corpus.tokens.get(w, 0) for w in item[1])),
    )[: args.count]
    rows = []
    for noun, mates in ranked:
        mates.sort(key=lambda w: -corpus.tokens.get(w, 0))
        total = corpus.tokens.get(noun, 0) + sum(corpus.tokens.get(w, 0) for w in mates)
        print(f'{noun}:{corpus.tokens.get(noun, 0)} -> ' + ', '.join(f'{w}:{corpus.tokens.get(w, 0)}' for w in mates)
              + f'  [{total}]')
        rows.append({'noun': noun, 'tokens': total, 'mates': ' '.join(mates)})
    if args.tsv and rows:
        with open(args.tsv, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
            writer.writeheader()
            writer.writerows(rows)


if __name__ == '__main__':
    main()
