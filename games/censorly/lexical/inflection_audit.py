"""Compare context-free inflection with occurrence identity.

Does not change puzzle payloads or masks. Writes review tables under
``lexical/data/`` and prints the qualification counts.
"""

from __future__ import annotations

import os
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.prelaunch import iter_fixtures
from games.censorly.lexical.resolver import prepare_target
from games.censorly.lexical.russian.context import (
    analyze_phrase,
    guess_inflection_ids,
    ungated_readings,
)
from games.censorly.lexical.russian.morphology import lexeme_ids
from games.censorly.tokenize import tokenize_text
from games.matcher.norm_matcher import MORPH_ANALYZER

DATA = Path(__file__).resolve().parent / 'data'


def main() -> int:
    started = time.perf_counter()
    bare_started = time.perf_counter()
    bare_surfaces = 0
    for _rel, _kind, body in iter_fixtures():
        for surface in dict.fromkeys(content_surfaces(body)):
            prepare_target(surface)
            bare_surfaces += 1
    bare_seconds = time.perf_counter() - bare_started

    index_started = time.perf_counter()
    occurrences = []
    for rel, _kind, body in iter_fixtures():
        tokens = tokenize_text(body)
        for item in analyze_phrase(body):
            if not item.lexemes and not item.surface:
                continue
            baseline = lexeme_ids(item.surface)
            occurrences.append({
                'article': rel,
                'position': item.position,
                'surface': item.surface,
                'context': sentence_of(tokens, item.position),
                'baseline': baseline,
                'lexemes': item.lexemes,
                'lemmas': item.lemmas,
                'ambiguous': item.ambiguous,
                'conservative': item.conservative,
                'signals': item.signals,
                'readings': reading_text(item.surface),
            })
    index_seconds = time.perf_counter() - index_started
    ambiguous = [item for item in occurrences if item['ambiguous']]
    unique_ambiguous = {fold(item['surface']) for item in ambiguous}

    corpus_paras = set()
    for item in occurrences:
        corpus_paras.update(para for para, _lemma in item['baseline'])
        corpus_paras.update(para for para, _lemma in item['lexemes'])
    guess_started = time.perf_counter()
    lemmas, ambiguous_forms = guesses_touching(corpus_paras)
    guess_collect_seconds = time.perf_counter() - guess_started

    by_para_base = defaultdict(list)
    by_para_new = defaultdict(list)
    conservative = []
    for index, item in enumerate(occurrences):
        if item['conservative']:
            conservative.append(index)
        else:
            for para, _lemma in item['lexemes']:
                by_para_new[para].append(index)
        for para, _lemma in item['baseline']:
            by_para_base[para].append(index)
    cons_by_para = defaultdict(list)
    for index in conservative:
        for para, _lemma in occurrences[index]['lexemes']:
            cons_by_para[para].append(index)

    removed = []
    added = []
    baseline_open = 0
    new_open = 0
    matched = defaultdict(list)
    lookup_started = time.perf_counter()
    guesses = list(dict.fromkeys([*lemmas, *ambiguous_forms]))
    print(f'guesses {len(guesses)} occurrences {len(occurrences)}', flush=True)
    for nth, guess in enumerate(guesses):
        if nth and nth % 20000 == 0:
            print(f'  looked up {nth}', flush=True)
        old_ids = lexeme_ids(guess)
        new_ids = guess_inflection_ids(guess)
        folded = fold(guess)
        base_hits = set()
        for para, lemma in old_ids:
            for index in by_para_base.get(para, ()):
                if (para, lemma) in occurrences[index]['baseline']:
                    base_hits.add(index)
        new_hits = set()
        for para, lemma in new_ids:
            for index in by_para_new.get(para, ()):
                item = occurrences[index]
                if item['conservative']:
                    continue
                if (para, lemma) in item['lexemes']:
                    new_hits.add(index)
        if new_ids and conservative:
            rare = min(new_ids, key=lambda ident: len(cons_by_para.get(ident[0], ())))
            for index in cons_by_para.get(rare[0], ()):
                item = occurrences[index]
                if item['lexemes'] <= new_ids:
                    new_hits.add(index)
        for index in base_hits:
            item = occurrences[index]
            if fold(item['surface']) == folded:
                continue
            if len(matched[index]) < 12:
                matched[index].append(guess)
            baseline_open += 1
            if index not in new_hits:
                removed.append(review_row(guess, item, old_ids, new_ids, removed=True))
        for index in new_hits:
            item = occurrences[index]
            if fold(item['surface']) == folded:
                continue
            new_open += 1
            if index not in base_hits:
                added.append(review_row(guess, item, old_ids, new_ids, removed=False))
    lookup_seconds = time.perf_counter() - lookup_started

    write_ambiguous(occurrences, matched)
    write_rows(DATA / 'inflection_removed.tsv', removed)
    write_rows(DATA / 'inflection_added.tsv', added)
    labels = defaultdict(int)
    for row in removed:
        labels[row[-1]] += 1
    add_labels = defaultdict(int)
    for row in added:
        add_labels[row[-1]] += 1
    bee_ok, bee_note = bee_check(occurrences)
    blockers = []
    if labels['BAD_REMOVAL']:
        blockers.append(f'BAD_REMOVAL {labels["BAD_REMOVAL"]}')
    if add_labels['BAD_ADD']:
        blockers.append(f'BAD_ADD {add_labels["BAD_ADD"]}')
    if not bee_ok:
        blockers.append(bee_note)
    verdict = (
        'READY FOR LOCAL FEATURE TEST' if not blockers
        else 'INFLECTION LAYER NOT QUALIFIED'
    )
    report = [
        f'fixtures 27',
        f'ambiguous_target_occurrences {len(ambiguous)}',
        f'unique_ambiguous_surfaces {len(unique_ambiguous)}',
        f'collision_candidates {len(removed)}',
        f'baseline_openings {baseline_open}',
        f'new_openings {new_open}',
        f'removed {len(removed)}',
        f'added {len(added)}',
        f'GOOD_REMOVAL {labels["GOOD_REMOVAL"]}',
        f'BAD_REMOVAL {labels["BAD_REMOVAL"]}',
        f'UNCLEAR {labels["UNCLEAR"]}',
        f'GOOD_ADD {add_labels["GOOD_ADD"]}',
        f'BAD_ADD {add_labels["BAD_ADD"]}',
        f'POLICY_B {add_labels["POLICY_B"]}',
        f'guesses {len(guesses)} lemmas {len(lemmas)} ambiguous_forms {len(ambiguous_forms)}',
        f'bare_prepare_seconds {bare_seconds:.2f} surfaces {bare_surfaces}',
        f'occurrence_index_seconds {index_seconds:.2f} occurrences {len(occurrences)}',
        f'guess_collect_seconds {guess_collect_seconds:.2f}',
        f'guess_lookup_seconds {lookup_seconds:.2f}',
        f'index_bytes {index_bytes(occurrences)}',
        f'peak_rss_kb {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}',
        f'total_seconds {time.perf_counter() - started:.1f}',
        bee_note,
        'verdict ' + verdict,
        *([f'blocker {item}' for item in blockers] or ['blockers none']),
    ]
    text = '\n'.join(report) + '\n'
    (DATA / 'inflection_report.txt').write_text(text, encoding='utf-8')
    sys.stdout.write(text)
    return 0 if not blockers else 1


def content_surfaces(body: str) -> list[str]:
    return [
        tok.get('surface') or ''
        for tok in tokenize_text(body)
        if tok.get('kind') == 'content' and tok.get('surface')
    ]


def sentence_of(tokens: list[dict], index: int) -> str:
    start = index
    while start > 0:
        prev = tokens[start - 1]
        if prev.get('kind') == 'punct' and (prev.get('surface') or '')[:1] in '.?!…':
            break
        start -= 1
    end = index + 1
    while end < len(tokens):
        tok = tokens[end]
        if tok.get('kind') == 'punct' and (tok.get('surface') or '')[:1] in '.?!…':
            end += 1
            break
        end += 1
    parts = []
    for tok in tokens[start:end]:
        surface = tok.get('surface') or ''
        if surface and not surface.isspace():
            parts.append(surface)
    return ' '.join(parts)[:300]


def reading_text(surface: str) -> str:
    parts = []
    for item in ungated_readings(surface):
        grams = ' '.join(
            piece for piece in (item.pos, item.case, item.number, item.gender, item.animacy)
            if piece
        )
        proper = ' proper' if item.proper else ''
        parts.append(
            f'{item.lemma} para={item.para_id} {grams}{proper} score={item.score:.3f}'
        )
    return ' || '.join(parts)


def guesses_touching(corpus_paras: set[int]) -> tuple[list[str], list[str]]:
    lemmas = set()
    ambiguous = []
    current = ''
    paras: set[int] = set()
    normals: set[str] = set()
    for word, _tag, normal, para, _idx in MORPH_ANALYZER.dictionary.iter_known_words():
        if word != current:
            _flush(current, paras, normals, corpus_paras, lemmas, ambiguous)
            current = word
            paras = set()
            normals = set()
        paras.add(int(para))
        normals.add(fold(normal))
    _flush(current, paras, normals, corpus_paras, lemmas, ambiguous)
    return sorted(lemmas), ambiguous


def _flush(word, paras, normals, corpus_paras, lemmas, ambiguous) -> None:
    if not word or not (paras & corpus_paras):
        return
    lemmas.update(normals)
    if len(paras) > 1:
        ambiguous.append(word)


def review_row(guess, item, old_ids, new_ids, *, removed: bool) -> list[str]:
    shared_old = sorted(lemma for para, lemma in old_ids if (para, lemma) in item['baseline'])
    if item['conservative']:
        # The guess contains every remaining identity. Policy B allows it.
        label = 'UNCLEAR' if removed else 'POLICY_B'
    elif removed:
        # Same lemma text with a different paradigm (Орёл / орёл) is a real close.
        label = 'BAD_REMOVAL' if (new_ids & item['lexemes']) else 'GOOD_REMOVAL'
    else:
        label = 'GOOD_ADD' if (new_ids & item['lexemes']) else 'BAD_ADD'
    return [
        guess,
        item['surface'],
        item['article'],
        item['context'],
        ','.join(shared_old),
        ','.join(sorted(item['lemmas'])),
        'conservative' if item['conservative'] else 'resolved',
        ' '.join(item['signals']),
        label,
    ]


def write_ambiguous(occurrences, matched) -> None:
    path = DATA / 'inflection_ambiguous.tsv'
    lines = ['article\tcontext\tsurface\treadings\tambiguous\tconservative\tsignals\tbaseline_guesses']
    for index, item in enumerate(occurrences):
        if not item['ambiguous']:
            continue
        lines.append('\t'.join([
            item['article'],
            item['context'].replace('\t', ' '),
            item['surface'],
            item['readings'].replace('\t', ' '),
            'yes',
            'yes' if item['conservative'] else 'no',
            ' '.join(item['signals']),
            ','.join(matched.get(index, ())),
        ]))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_rows(path: Path, rows: list[list[str]]) -> None:
    header = (
        'guess\ttarget\tarticle\tcontext\tshared_baseline\t'
        'target_lemmas\tstatus\tsignals\tlabel'
    )
    body = ['\t'.join(cell.replace('\t', ' ').replace('\n', ' ') for cell in row) for row in rows]
    path.write_text(header + '\n' + '\n'.join(body) + ('\n' if body else ''), encoding='utf-8')


def bee_check(occurrences) -> tuple[bool, str]:
    from games.censorly.lexical.russian.context import inflection_matches

    hits = [
        item for item in occurrences
        if item['article'].endswith('articles/bee.txt')
        and fold(item['surface']) == 'белки'
        and 'особые' in item['context']
    ]
    if not hits:
        return False, 'bee особые белки not found'
    item = hits[0]
    opened_protein = 'белок' in item['lemmas'] and not item['conservative']
    # Re-check through the public matcher using the stored identity.
    from games.censorly.lexical.russian.context import TargetOccurrence
    occurrence = TargetOccurrence(
        item['surface'], item['lexemes'], item['ambiguous'], item['conservative'], item['signals'],
    )
    protein = inflection_matches('белок', occurrence)
    squirrel = inflection_matches('белка', occurrence)
    ok = opened_protein and protein and not squirrel
    return ok, (
        f'bee особые белки lemmas={sorted(item["lemmas"])} '
        f'signals={" ".join(item["signals"])} белок={protein} белка={squirrel}'
    )


def index_bytes(occurrences) -> int:
    total = 0
    for item in occurrences:
        total += len(item['surface']) + len(item['context']) + len(item['readings'])
        total += 64 * (len(item['baseline']) + len(item['lexemes']))
    return total


if __name__ == '__main__':
    sys.exit(main())
