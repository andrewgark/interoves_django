"""Generate ``lexical/data/adverb_bases.tsv``: rootless adverbs → rooted adjectives.

Dictionaries of roots list adjectives but rarely their adverbs, so
«обычно», «исторически», «по-новому» have no game root. The rule links an
adverb (or predicative) to its adjective only on morphological evidence:

``short_form``  pymorphy also parses the adverb as the neuter short form of
                the adjective (быстро ↔ быстрый, крайне ↔ крайний);
``ski``         «-ски» / «по-…-ски» ↔ «-ский» (исторически, по-русски);
``po_omu``      «по-…-ому/-ему» ↔ the adjective (по-новому ↔ новый).

The adjective must already have game roots; the adverb must have none, and
the live matcher must not root it another way already (pymorphy's short
adjective reading, a hyphen structure), so every row adds coverage.
Senses that diverged (конечно ≠ конечный) live in
``adverb_rule_exceptions.tsv`` with a reason and are never generated.
Reviewed nest anchors win over the rule. Run after changing roots::

    ../venv/interoves_django/bin/python -m \
        games.puzzles.censorly.offline.tools.adverb_rule [--review /tmp/adv.tsv]
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
from django.apps import apps

if not apps.ready:
    django.setup()

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.match.rootbank import provenance
from games.puzzles.censorly.offline.corpus import load_corpus
from games.puzzles.core.norm_matcher import MORPH_ANALYZER

DATA = Path(__file__).resolve().parents[2] / 'lexical' / 'data'
OUTPUT = DATA / 'adverb_bases.tsv'
EXCEPTIONS = DATA / 'adverb_rule_exceptions.tsv'
_POS = frozenset({'ADVB', 'PRED'})


def _own_structures(lemma: str, prov: dict) -> bool:
    """Roots that do not come from this rule itself."""
    info = prov.get(lemma)
    return bool(info) and info['source'] not in {
        'UNRESOLVED', 'UNRESOLVED_TOO_AMBIGUOUS', 'ADVERB_OF_ADJECTIVE',
    } and not info['source'].startswith('UNRESOLVED')


def _rooted(lemma: str, prov: dict) -> bool:
    info = prov.get(lemma)
    return bool(info) and not info['source'].startswith('UNRESOLVED')


def adverb_lemmas() -> set[str]:
    found = set()
    for _word, tag, normal, _para, _idx in MORPH_ANALYZER.dictionary.iter_known_words():
        if tag.POS in _POS:
            found.add(fold(normal))
    return found


def candidates(adverb: str, prov: dict) -> list[tuple[str, str]]:
    found = []
    for parse in MORPH_ANALYZER.parse(adverb):
        tag = parse.tag
        if tag.POS == 'ADJS' and 'neut' in tag and 'sing' in tag:
            adjective = fold(parse.normal_form)
            if adjective != adverb and _rooted(adjective, prov):
                found.append((adjective, 'short_form'))
    stem = adverb.removeprefix('по-')
    if stem.endswith(('ски', 'цки')):
        adjective = stem + 'й'
        if _rooted(adjective, prov):
            found.append((adjective, 'ski'))
    if adverb.startswith('по-') and stem.endswith(('ому', 'ему')):
        for ending in ('ый', 'ий', 'ой'):
            adjective = stem[:-3] + ending
            if _rooted(adjective, prov):
                found.append((adjective, 'po_omu'))
    unique = []
    for item in found:
        if item[0] not in {adjective for adjective, _kind in unique}:
            unique.append(item)
    return unique


def covered_without_rule(adverb: str) -> bool:
    """The live matcher already roots this adverb another way.

    pymorphy's neuter short-adjective reading (примерно → примерный) and the
    hyphen structure (по-новому) give the article token roots without a row
    here; such adverbs need no rule entry.
    """
    from games.puzzles.censorly.lexical.match import ANALYZER
    from games.puzzles.censorly.lexical.match.rootbank import structures_of

    pack = ANALYZER.target(adverb, article_lemma=adverb)
    if any(structures_of(item.lemma) for item in pack.reads if fold(item.lemma) != adverb):
        return True
    return bool(ANALYZER.hyphen_structures(adverb))


def load_exceptions() -> dict[str, str]:
    if not EXCEPTIONS.exists():
        return {}
    with EXCEPTIONS.open(encoding='utf-8', newline='') as stream:
        return {fold(row['adverb']): row['reason'] for row in csv.DictReader(stream, delimiter='\t')}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--review', type=Path, help='also write a corpus-ranked review sheet')
    args = parser.parse_args()

    prov = provenance()
    exceptions = load_exceptions()
    corpus = load_corpus()
    rows, ambiguous = [], []
    redundant = 0
    for adverb in sorted(adverb_lemmas()):
        if adverb in reviewed.NEST_ANCHORS.data or adverb in exceptions or _own_structures(adverb, prov):
            continue
        found = candidates(adverb, prov)
        if found and covered_without_rule(adverb):
            redundant += 1
            continue
        if len(found) == 1:
            rows.append((adverb, found[0][0], found[0][1]))
        elif len(found) > 1:
            ambiguous.append((adverb, found))

    with OUTPUT.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['adverb', 'adjective', 'evidence'])
        writer.writerows(rows)
    if args.review:
        with args.review.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
            writer.writerow(['adverb', 'corpus_count', 'adjective', 'evidence', 'adjective_structures'])
            from games.puzzles.censorly.lexical.match.rootbank import structures_of
            ranked = sorted(rows, key=lambda row: (-corpus.tokens.get(row[0], 0), row[0]))
            for adverb, adjective, evidence in ranked:
                writer.writerow([
                    adverb, corpus.tokens.get(adverb, 0), adjective, evidence,
                    ';'.join('+'.join(item) for item in structures_of(adjective)),
                ])
    covered = sum(corpus.tokens.get(adverb, 0) for adverb, _adjective, _kind in rows)
    print(
        f'adverb_bases={len(rows)} already_rooted_at_runtime={redundant} '
        f'ambiguous_skipped={len(ambiguous)} '
        f'exceptions={len(exceptions)} previous={len(reviewed.ADVERB_BASES.data)} corpus_tokens={covered}'
    )
    for adverb, found in ambiguous[:20]:
        print('  ambiguous:', adverb, found)


if __name__ == '__main__':
    main()
