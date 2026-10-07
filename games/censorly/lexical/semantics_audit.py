"""Compare the simplified resolver with the previous graph resolver.

Does not change masks. Writes a short report under lexical/data.
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.prelaunch import content_surfaces, iter_fixtures
from games.censorly.lexical.relations.graph import neighbors
from games.censorly.lexical.resolver import open_prepared, prepare_target, relation_kind
from games.censorly.lexical.rootbank import load_seconds, structures_of
from games.censorly.lexical.semantics import explain, initially_open, readings_of
from games.censorly.tokenize import tokenize_text

DATA = Path(__file__).resolve().parent / 'data'
PROBES = (
    ('пчела', 'пчеловод'),
    ('пчела', 'пчеловодство'),
    ('мёд', 'медоносный'),
    ('вода', 'водопад'),
    ('белка', 'белки'),
    ('белок', 'белки'),
    ('ходить', 'выходить'),
    ('строить', 'три'),
    ('пара', 'испарять'),
    ('мать', 'матка'),
    ('мать', 'матушка'),
    ('семь', 'семья'),
)


def main() -> int:
    started = time.perf_counter()
    root_load = load_seconds()
    articles = list(iter_fixtures())
    prep_started = time.perf_counter()
    occurrences = []
    unique = []
    seen = set()
    service = Counter()
    service_examples = defaultdict(list)
    for rel, _kind, body in articles:
        tokens = tokenize_text(body)
        for tok in tokens:
            if tok.get('kind') not in ('content', 'stop') or not tok.get('surface'):
                continue
            surface = tok['surface']
            occurrences.append((rel, surface, tok.get('kind')))
            if fold(surface) not in seen:
                seen.add(fold(surface))
                unique.append(surface)
    index_started = time.perf_counter()
    prepared = {fold(surface): prepare_target(surface) for surface in unique}
    prep_seconds = time.perf_counter() - prep_started
    new_index, service_surfaces = _new_index(unique)
    for rel, surface, kind in occurrences:
        if fold(surface) in service_surfaces:
            reads = readings_of(surface)
            pos = reads[0].pos if reads else '?'
            service[pos] += 1
            if len(service_examples[pos]) < 8 and surface not in service_examples[pos]:
                service_examples[pos].append(surface)
    index_seconds = time.perf_counter() - index_started

    # Probe guesses: every lemma that occurs, plus graph neighbors, plus the
    # readings of those lemmas. This is the corpus-relevant slice of the
    # prelaunch set; the full 193k cognate fuzz is unchanged and separate.
    guesses = set()
    for surface in unique:
        guesses.add(fold(surface))
        for item in readings_of(surface):
            guesses.add(item.lemma)
            for neighbour in neighbors(item.lemma):
                guesses.add(neighbour)
    old_index = _old_index(prepared, service_surfaces)
    guess_started = time.perf_counter()
    old_rows = Counter()
    new_rows = Counter()
    added = Counter()
    removed = Counter()
    samples = []
    print(f'guesses {len(guesses)}', flush=True)
    for nth, guess in enumerate(guesses):
        if not guess or initially_open(guess):
            continue
        if nth and nth % 5000 == 0:
            print(f'  {nth}', flush=True)
        old = _lookup_old(guess, old_index)
        new = _lookup(guess, new_index)
        old = {item for item in old if fold(item) != fold(guess)}
        new = {item for item in new if fold(item) != fold(guess)}
        old_rows['rows'] += len(old)
        new_rows['rows'] += len(new)
        extra = new - old
        for surface in extra:
            reason = explain(guess, surface) or 'other'
            added[reason] += 1
            if len(samples) < 30:
                samples.append((guess, surface, reason))
        removed['rows'] += len(old - new)
    lookup_seconds = time.perf_counter() - guess_started
    leaks = _compound_leaks()
    lines = [
        f'root_load_seconds {root_load:.2f}',
        f'unique_surfaces {len(unique)} occurrences {len(occurrences)}',
        f'old_prepare_seconds {prep_seconds:.2f}',
        f'new_index_seconds {index_seconds - prep_seconds:.2f}',
        f'guesses {len(guesses)} lookup_seconds {lookup_seconds:.2f}',
        f'old_openings {old_rows["rows"]}',
        f'new_openings {new_rows["rows"]}',
        f'added {sum(added.values())} ' + ' '.join(f'{key}={count}' for key, count in added.most_common()),
        f'removed {removed["rows"]}',
        f'service_occurrences {sum(service.values())}',
    ]
    for pos, count in service.most_common():
        lines.append(f'  service {pos} {count} examples={",".join(service_examples[pos])}')
    lines.append('probes')
    for guess, target in PROBES:
        lines.append(
            f'  {guess} / {target} old={relation_kind(guess, target) or "closed"} '
            f'new={explain(guess, target) or "closed"}'
        )
    graph_kept = graph_lost = 0
    lost_samples = []
    import gzip
    graph_path = DATA / 'ru_graph.tsv.gz'
    with gzip.open(graph_path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            left, right, proof = line.rstrip('\n').split('\t')
            if explain(left, right):
                graph_kept += 1
            else:
                graph_lost += 1
                if len(lost_samples) < 25:
                    lost_samples.append(f'{left} / {right} {proof}')
    lines.append(f'graph_edges_kept {graph_kept} graph_edges_lost {graph_lost}')
    for row in lost_samples:
        lines.append('  lost ' + row)
    lines.append(f'compound_leaks {len(leaks)}')
    for row in leaks[:15]:
        lines.append('  leak ' + row)
    lines.append('added_samples')
    for guess, surface, reason in samples:
        lines.append(f'  {reason} {guess} → {surface}')
    lines.append(f'total_seconds {time.perf_counter() - started:.1f}')
    blockers = []
    if leaks:
        blockers.append(f'compound leaks {len(leaks)}')
    if explain('пчела', 'пчеловод') or explain('мёд', 'медоносный') or explain('вода', 'водопад'):
        blockers.append('compound subset still opens')
    verdict = 'SIMPLIFIED SEMANTICS READY FOR LOCAL INTEGRATION' if not blockers else 'SIMPLIFIED SEMANTICS NOT READY'
    lines.append('verdict ' + verdict)
    text = '\n'.join(lines) + '\n'
    (DATA / 'semantics_report.txt').write_text(text, encoding='utf-8')
    sys.stdout.write(text)
    return 0 if not blockers else 1


def _new_index(surfaces: list[str]):
    by_lexeme = defaultdict(set)
    by_root = defaultdict(set)
    by_lemma = defaultdict(set)
    by_fold = defaultdict(set)
    service = set()
    for surface in surfaces:
        folded = fold(surface)
        if initially_open(surface):
            service.add(folded)
            continue
        by_fold[folded].add(surface)
        for item in readings_of(surface):
            by_lexeme[(item.para_id, item.lemma)].add(surface)
            by_lemma[item.lemma].add(surface)
        from games.censorly.lexical.semantics import _roots
        for structure in _roots(readings_of(surface), surface):
            by_root[structure].add(surface)
    return {
        'fold': by_fold,
        'lexeme': by_lexeme,
        'root': by_root,
        'lemma': by_lemma,
    }, service


def _old_index(prepared: dict, service: set[str]):
    from games.censorly.lexical.resolver import _cognate_closure

    by_exact = defaultdict(set)
    by_para = defaultdict(set)
    conservative = []
    by_cognate_lemma = defaultdict(set)
    for folded, item in prepared.items():
        if folded in service:
            continue
        surface = str(item['surface'])
        by_exact[folded].add(surface)
        lexemes = item.get('inflection_lexemes') or frozenset()
        if item.get('inflection_conservative'):
            conservative.append((surface, lexemes))
        else:
            for para, lemma in lexemes:
                by_para[(para, lemma)].add(surface)
        for lemma in item.get('lemmas') or ():
            by_cognate_lemma[lemma].add(surface)
    return {
        'exact': by_exact,
        'para': by_para,
        'conservative': conservative,
        'cognate': by_cognate_lemma,
        'closure': _cognate_closure,
        'prepared': prepared,
    }


def _lookup_old(guess: str, index) -> set[str]:
    from games.censorly.lexical.russian.context import guess_inflection_ids

    found = set(index['exact'].get(fold(guess), ()))
    ids = guess_inflection_ids(guess)
    for ident in ids:
        found.update(index['para'].get(ident, ()))
    if ids and index['conservative']:
        for surface, needed in index['conservative']:
            if needed and needed <= ids:
                found.add(surface)
    closure = index['closure'](guess)
    if closure:
        candidates = set()
        for lemma in closure:
            candidates.update(index['cognate'].get(lemma, ()))
        for surface in candidates:
            lemmas = index['prepared'][fold(surface)].get('lemmas') or frozenset()
            if lemmas and lemmas <= closure and fold(guess) not in {fold(lemma) for lemma in lemmas}:
                # Cognate closure includes the guess lemmas; a target whose
                # every lemma is linked (or is the guess) was an old opening.
                if lemmas <= closure and not lemmas <= {fold(guess)}:
                    found.add(surface)
    return found


def _lookup(guess: str, index) -> set[str]:
    from games.censorly.lexical.semantics import _roots, readings_of as reads
    found = set(index['fold'].get(fold(guess), ()))
    parsed = reads(guess)
    for item in parsed:
        found.update(index['lexeme'].get((item.para_id, item.lemma), ()))
    for structure in _roots(parsed, guess):
        found.update(index['root'].get(structure, ()))
    return found


def _compound_leaks() -> list[str]:
    """A one-root structure must not be stored as a reading of a compound."""
    from games.censorly.lexical.rootbank import _bank

    bank, _seconds = _bank()
    leaks = []
    for lemma, structs in bank.items():
        multi = [item for item in structs if len(item) >= 2]
        single = [item for item in structs if len(item) == 1]
        if not multi:
            continue
        for one in single:
            if any(one[0] in item for item in multi):
                leaks.append(f'{lemma} keeps {one} beside {multi[0]}')
                break
    return leaks


if __name__ == '__main__':
    sys.exit(main())
