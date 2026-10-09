"""Exhaustive audit for the two reviewed missing-root assignments.

This deliberately does not enumerate the pymorphy dictionary.  It enumerates
only the reviewed lemmas' lexemes and the already available fixture target
universe, then compares resolver output with both reviewed mappings enabled
and disabled in-process.
"""

from __future__ import annotations

import json
import os
import resource
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical import family_audit as audit
from games.censorly.lexical import rootbank
from games.censorly.lexical.core import fold
from games.censorly.lexical.match import _GuessPack, _Pack, _relation
from games.censorly.lexical.rootbank import assignment_of, structures_of
from games.censorly.lexical.semantics import REVIEWED_DERIVED_ROOTS, readings_of
from games.matcher.norm_matcher import MORPH_ANALYZER

OPAQUE = 'tikh:сенат'
DERIVED = 'fam:иволг'
KEYS = {OPAQUE, DERIVED}


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _clear_bank():
    rootbank._bank.cache_clear()


@contextmanager
def _config(enabled: bool):
    old_opaque = rootbank.REVIEWED_OPAQUE_ROOTS
    old_derived = dict(rootbank.REVIEWED_DERIVED_ROOTS)
    if enabled:
        rootbank.REVIEWED_OPAQUE_ROOTS = frozenset({'сенат'})
        rootbank.REVIEWED_DERIVED_ROOTS.clear()
        rootbank.REVIEWED_DERIVED_ROOTS.update(old_derived)
    else:
        rootbank.REVIEWED_OPAQUE_ROOTS = frozenset()
        rootbank.REVIEWED_DERIVED_ROOTS.clear()
    _clear_bank()
    try:
        yield
    finally:
        rootbank.REVIEWED_OPAQUE_ROOTS = old_opaque
        rootbank.REVIEWED_DERIVED_ROOTS.clear()
        rootbank.REVIEWED_DERIVED_ROOTS.update(old_derived)
        _clear_bank()


def _forms(lemma: str) -> set[str]:
    forms = set()
    for parse in MORPH_ANALYZER.parse(fold(lemma)):
        try:
            lexeme = parse.lexeme
        except AttributeError:
            continue
        for item in lexeme:
            forms.add(fold(item.word))
    forms.add(fold(lemma))
    return forms


def _reviewed_lemmas():
    bank, _ = rootbank._bank()
    opaque = sorted(
        lemma for lemma, structures in bank.items()
        if structures == ((OPAQUE,),)
        and assignment_of(lemma)['source'] == 'REVIEWED_OPAQUE_ROOT'
    )
    return opaque


def _target_rows(corpus):
    _index, occurrences = audit.build_target_index(corpus)
    return occurrences


def _pack_rows(rows, guess=False):
    cls = _GuessPack if guess else _Pack
    return {surface: cls(surface) for _rel, surface, _lemmas, _families in rows}


def _rows_for_keys(rows, packs):
    return [row for row in rows if _root_ids(packs[row[1]].roots) & KEYS]


def _root_ids(structures):
    return {part for structure in structures for part in structure}


def _relation_rows(guess_surfaces, target_rows, guess_packs, target_packs):
    result = []
    for guess in sorted(guess_surfaces):
        gp = guess_packs[guess]
        for rel, target, lemmas, _families in target_rows:
            reason = _relation(gp, target_packs[target])
            if reason:
                result.append({
                    'guess': guess,
                    'target': target,
                    'reason': reason,
                    'guess_roots': sorted(gp.roots),
                    'target_roots': sorted(target_packs[target].roots),
                    'target_lemmas': sorted(lemmas),
                    'article': rel,
                })
    return result


def _key_provenance(row):
    roots = set(row['guess_roots']) | set(row['target_roots'])
    if OPAQUE in roots:
        return 'REVIEWED_OPAQUE_ROOT'
    if DERIVED in roots:
        return 'REVIEWED_DERIVED_ROOT'
    return ''


def main():
    started = time.perf_counter()
    corpus = audit.load_corpus()
    target_index, target_rows = audit.build_target_index(corpus)

    # Build the after bank first and obtain the complete opaque family.
    opaque_lemmas = _reviewed_lemmas()
    opaque_forms = set().union(*(_forms(lemma) for lemma in opaque_lemmas))
    derived_forms = _forms('иволговый')
    affected_guess_surfaces = opaque_forms | derived_forms

    affected_indices = set()
    for key in KEYS:
        affected_indices.update(target_index['root'].get((key,), ()))
    affected_rows = [target_rows[index] for index in sorted(affected_indices)]
    affected_target_surfaces = {row[1] for row in affected_rows}

    configs = {}
    for name, enabled in (('before', False), ('after', True)):
        with _config(enabled):
            target_packs = _pack_rows(affected_rows)
            guess_packs = _pack_rows(
                [(None, surface, set(), set()) for surface in affected_guess_surfaces],
                guess=True,
            )
            configs[name] = (guess_packs, target_packs)

    before_guesses, before_targets = configs['before']
    after_guesses, after_targets = configs['after']

    after_affected_targets = affected_rows
    before_affected_targets = affected_rows
    target_cone = affected_target_surfaces

    # Forward cone: every reviewed-derived/opaque guess form against every
    # target in the current fixture target universe.
    before_forward = _relation_rows(affected_guess_surfaces, target_rows, before_guesses, before_targets)
    after_forward = _relation_rows(affected_guess_surfaces, target_rows, after_guesses, after_targets)

    # Reverse cone: every existing target-universe surface as a guess against
    # every target whose complete structure contains an affected key.
    # Reverse cone from the already-built target index.  Only surfaces sharing
    # an exact, lexeme or complete-root key with an affected target can change.
    reverse_candidates = set()
    for target in affected_target_surfaces:
        target_pack = after_targets[target]
        reverse_candidates.update(
            surface for index_key in (target_pack.fold,)
            for index_map in (target_index['fold'],)
            for idx in index_map.get(index_key, ())
            for _rel, surface, _lemmas, _families in (target_rows[idx],)
        )
        for key in target_pack.lexemes:
            reverse_candidates.update(
                target_rows[idx][1] for idx in target_index['lexeme'].get(key, ())
            )
        for key in target_pack.roots:
            reverse_candidates.update(
                target_rows[idx][1] for idx in target_index['root'].get(key, ())
            )
    with _config(False):
        reverse_before_packs = _pack_rows(
            [(None, s, set(), set()) for s in reverse_candidates], guess=True
        )
    with _config(True):
        reverse_after_packs = _pack_rows(
            [(None, s, set(), set()) for s in reverse_candidates], guess=True
        )
    reverse_before = _relation_rows(reverse_candidates, before_affected_targets, reverse_before_packs, before_targets)
    reverse_after = _relation_rows(reverse_candidates, after_affected_targets, reverse_after_packs, after_targets)

    def signature(row):
        return (row['guess'], row['target'], row['article'], row['reason'])

    before_set = {signature(row) for row in before_forward + reverse_before}
    after_set = {signature(row) for row in after_forward + reverse_after}
    new_signatures = after_set - before_set
    new_rows = [row for row in after_forward + reverse_after if signature(row) in new_signatures]
    unique_new = {}
    for row in new_rows:
        unique_new.setdefault(signature(row), row)

    bridge_forms = {
        surface: sorted({item.normal_form for item in MORPH_ANALYZER.parse(surface)
                         if fold(item.normal_form) in REVIEWED_DERIVED_ROOTS})
        for surface in sorted(derived_forms)
    }
    bridge_leaks = {
        surface: sorted({fold(item.normal_form) for item in MORPH_ANALYZER.parse(surface)
                         if fold(item.normal_form) not in REVIEWED_DERIVED_ROOTS
                         and 'иволгов' in fold(item.normal_form)})
        for surface in sorted(derived_forms)
    }

    adversarial = (
        ('страна', 'странный'), ('крупа', 'крупный'), ('свет', 'светский'),
        ('мать', 'матка'), ('червь', 'червовый'), ('мара', 'маревый'),
        ('год', 'годный'), ('душа', 'душный'), ('пчела', 'пчеловод'),
        ('мёд', 'медоносный'), ('мир', 'море'),
    )
    adversarial_rows = []
    from games.censorly.lexical.semantics import opens
    for left, right in adversarial:
        adversarial_rows.append({'guess': left, 'target': right, 'open': opens(left, right)})

    output = {
        'elapsed_seconds': round(time.perf_counter() - started, 3),
        'peak_rss_mb': round(rss_mb(), 1),
        'affected_root_keys': sorted(KEYS),
        'affected_lemmas': {
            'opaque': opaque_lemmas,
            'derived': ['иволговый'],
        },
        'affected_guess_surfaces': {
            'opaque_count': len(opaque_forms),
            'derived_count': len(derived_forms),
            'opaque': sorted(opaque_forms),
            'derived': sorted(derived_forms),
        },
        'target_universe_occurrences': len(target_rows),
        'affected_targets': {
            'opaque': sorted({row[1] for row in after_affected_targets if OPAQUE in _root_ids(after_targets[row[1]].roots)}),
            'derived': sorted({row[1] for row in after_affected_targets if DERIVED in _root_ids(after_targets[row[1]].roots)}),
            'count': len(target_cone),
        },
        'before_relations': len(before_set),
        'after_relations': len(after_set),
        'new_relations': len(unique_new),
        'new_relations_by_provenance': {
            key: sum(
                (('REVIEWED_DERIVED_LEMMA_BRIDGE' if row['guess'] != 'иволговый' and DERIVED in _root_ids(row['guess_roots'])
                  else _key_provenance(row)) == key)
                for row in unique_new.values()
            )
            for key in ('REVIEWED_OPAQUE_ROOT', 'REVIEWED_DERIVED_ROOT', 'REVIEWED_DERIVED_LEMMA_BRIDGE')
        },
        'unexpected_relations': [row for row in unique_new.values() if not (_root_ids(row['guess_roots']) | _root_ids(row['target_roots'])) <= KEYS],
        'new_relation_rows': sorted(unique_new.values(), key=lambda row: (row['guess'], row['target'], row['article'])),
        'bridge_forms': bridge_forms,
        'bridge_leaks': bridge_leaks,
        'adversarial': adversarial_rows,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    Path('/tmp/censorly-impact-cone-audit.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


if __name__ == '__main__':
    main()
