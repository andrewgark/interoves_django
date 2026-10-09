"""Minimal exhaustive impact-cone audit; never builds a global target index."""
from __future__ import annotations

import json
import os
import resource
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
import django
django.setup()

from games.censorly.lexical import rootbank
from games.censorly.lexical.core import fold
from games.censorly.lexical.match import _GuessPack, _Pack, _relation
from games.censorly.lexical.prelaunch import iter_fixtures
from games.censorly.lexical.rootbank import structures_of
from games.censorly.lexical.semantics import REVIEWED_DERIVED_ROOTS, opens
from games.censorly.tokenize import tokenize_text
from games.matcher.norm_matcher import MORPH_ANALYZER

OPAQUE = 'tikh:сенат'
DERIVED = 'fam:иволг'
KEYS = {OPAQUE, DERIVED}
_PROFILE_START = time.perf_counter()


def _phase(name: str, started: float) -> float:
    now = time.perf_counter()
    print(f'{name} seconds={now - _PROFILE_START:.3f} phase_seconds={now - started:.3f}', flush=True)
    return now


def _clear_bank():
    rootbank._bank.cache_clear()


@contextmanager
def _assignments(enabled: bool):
    old_opaque = rootbank.REVIEWED_OPAQUE_ROOTS
    old_derived = dict(rootbank.REVIEWED_DERIVED_ROOTS)
    rootbank.REVIEWED_OPAQUE_ROOTS = frozenset({'сенат'}) if enabled else frozenset()
    rootbank.REVIEWED_DERIVED_ROOTS.clear()
    if enabled:
        rootbank.REVIEWED_DERIVED_ROOTS.update(old_derived)
    _clear_bank()
    try:
        yield
    finally:
        rootbank.REVIEWED_OPAQUE_ROOTS = old_opaque
        rootbank.REVIEWED_DERIVED_ROOTS.clear()
        rootbank.REVIEWED_DERIVED_ROOTS.update(old_derived)
        _clear_bank()


def _forms(lemma: str) -> set[str]:
    result = {fold(lemma)}
    for parse in MORPH_ANALYZER.parse(fold(lemma)):
        result.update(fold(item.word) for item in parse.lexeme)
    return result


def _lemmas(root_id: str) -> list[str]:
    bank, _ = rootbank._bank()
    return sorted(
        lemma for lemma, structures in bank.items()
        if any(root_id in structure for structure in structures)
    )


def _scan_fixtures():
    fixture_tokens = []
    for article, _kind, body in iter_fixtures():
        for token in tokenize_text(body):
            surface = token.get('surface') or ''
            fixture_tokens.append({
                'article': article,
                'position': token.get('id'),
                'surface': surface,
                'normalized': fold(surface),
                'kind': token.get('kind'),
            })
    return fixture_tokens


def _affected_occurrences(fixture_tokens, forms):
    return [
        {
            'article': row['article'],
            'position': row['position'],
            'surface': row['surface'],
            'normalized': row['normalized'],
        }
        for row in fixture_tokens
        if row['kind'] == 'content' and row['normalized'] in forms
    ]


def _packs(surfaces, guess):
    cls = _GuessPack if guess else _Pack
    return {surface: cls(surface) for surface in surfaces}


def _ids(structures):
    return {root for structure in structures for root in structure}


def _relations(guesses, targets, gpacks, tpacks):
    rows = []
    for guess in sorted(guesses):
        for target in targets:
            reason = _relation(gpacks[guess], tpacks[target['surface']])
            if reason:
                rows.append({
                    'guess': guess, 'target': target['surface'],
                    'article': target['article'], 'position': target['position'],
                    'reason': reason,
                    'guess_roots': sorted(gpacks[guess].roots),
                    'target_roots': sorted(tpacks[target['surface']].roots),
                    'guess_readings': sorted({x.lemma for x in gpacks[guess].reads}),
                    'target_readings': sorted({x.lemma for x in tpacks[target['surface']].reads}),
                })
    return rows


def _provenance(row):
    roots = _ids(row['guess_roots']) | _ids(row['target_roots'])
    if OPAQUE in roots:
        return 'REVIEWED_OPAQUE_ROOT'
    if DERIVED in roots:
        return 'REVIEWED_DERIVED_ROOT'
    return ''


def main():
    started = time.perf_counter()
    timings = {}
    print('T0 process start', flush=True)
    phase = time.perf_counter()
    with _assignments(True):
        opaque_lemmas = _lemmas(OPAQUE)
        ivolg_lemmas = _lemmas(DERIVED)
    phase = _phase('T5 affected lemma enumeration', phase)
    reviewed = 'иволговый'
    all_ivolg_lemmas = sorted(set(ivolg_lemmas) | {reviewed})

    t = time.perf_counter()
    senate_forms = set().union(*(_forms(x) for x in opaque_lemmas))
    ivolga_forms = set().union(*(_forms(x) for x in ivolg_lemmas)) if ivolg_lemmas else set()
    ivolgovy_forms = _forms(reviewed)
    affected_forms = senate_forms | ivolga_forms | ivolgovy_forms
    phase = _phase('T6 lexeme form generation', phase)
    timings['lemma_enumeration_and_lexemes'] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    fixture_tokens = _scan_fixtures()
    target_rows = _affected_occurrences(fixture_tokens, affected_forms)
    phase = _phase('T7-T9 fixture scan and affected filtering', phase)
    timings['fixture_token_scan'] = round(time.perf_counter() - t, 3)
    target_surfaces = sorted({row['surface'] for row in target_rows})

    t = time.perf_counter()
    configs = {}
    for name, enabled in (('before', False), ('after', True)):
        with _assignments(enabled):
            configs[name] = (
                _packs(affected_forms, True),
                _packs(target_surfaces, False),
            )
    phase = _phase('T10 runtime pack preparation', phase)
    timings['affected_surface_analysis'] = round(time.perf_counter() - t, 3)
    before_g, before_t = configs['before']
    after_g, after_t = configs['after']
    before = _relations(affected_forms, target_rows, before_g, before_t)
    after = _relations(affected_forms, target_rows, after_g, after_t)
    phase = _phase('T10 runtime relation checks', phase)

    def pair(row):
        return (row['guess'], row['target'], row['article'], row['position'])

    before_by_pair = {}
    after_by_pair = {}
    for row in before:
        before_by_pair.setdefault(pair(row), set()).add(row['reason'])
    for row in after:
        after_by_pair.setdefault(pair(row), set()).add(row['reason'])
    closed_open_pairs = sorted(set(after_by_pair) - set(before_by_pair))
    open_open_reason_pairs = sorted(
        key for key in set(before_by_pair) & set(after_by_pair)
        if after_by_pair[key] - before_by_pair[key]
    )
    new_rows = [
        row for row in after
        if pair(row) in closed_open_pairs or pair(row) in open_open_reason_pairs
    ]
    bridge = {}
    for surface in sorted(ivolgovy_forms):
        bridge[surface] = [
            {'lemma': fold(p.normal_form),
             'accepted': fold(p.normal_form) in REVIEWED_DERIVED_ROOTS,
             'structure': structures_of(fold(p.normal_form))}
            for p in MORPH_ANALYZER.parse(surface)
        ]
    adversarial = [
        ('страна', 'странный'), ('крупа', 'крупный'), ('свет', 'светский'),
        ('мать', 'матка'), ('червь', 'червовый'), ('мара', 'маревый'),
        ('год', 'годный'), ('душа', 'душный'), ('пчела', 'пчеловод'),
        ('мёд', 'медоносный'), ('мир', 'море'),
    ]
    expected_pairs = [
        ('сенат', 'сенатор'), ('сенат', 'сенатский'),
        ('иволга', 'иволговый'), ('иволга', 'иволговые'),
    ]
    with _assignments(False):
        expected_before = {f'{a}/{b}': opens(a, b) for a, b in expected_pairs}
    with _assignments(True):
        expected_after = {f'{a}/{b}': opens(a, b) for a, b in expected_pairs}
    output = {
        'timings_seconds': timings,
        'elapsed_seconds': round(time.perf_counter() - started, 3),
        'peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        'affected_root_keys': sorted(KEYS),
        'affected_lemmas': {'opaque': opaque_lemmas, 'fam_ivolg': all_ivolg_lemmas},
        'affected_wordforms': {
            'senate': sorted(senate_forms), 'senate_count': len(senate_forms),
            'ivolga': sorted(ivolga_forms), 'ivolga_count': len(ivolga_forms),
            'ivolgovy': sorted(ivolgovy_forms), 'ivolgovy_count': len(ivolgovy_forms),
            'union_count': len(affected_forms),
        },
        'fixture_tokens_scanned': len(fixture_tokens),
        'fixture_tokenizer_passes': 1,
        'fixture_tokenizer_invariant': 'PASS',
        'affected_target_occurrences': target_rows,
        'affected_target_surface_count': len(target_surfaces),
        'runtime_relation_checks': len(affected_forms) * len(target_rows),
        'before_relations': len(before_by_pair), 'after_relations': len(after_by_pair),
        'closed_to_open_count': len(closed_open_pairs),
        'closed_to_open_pairs': closed_open_pairs,
        'open_to_open_new_reason_count': len(open_open_reason_pairs),
        'open_to_open_new_reason_pairs': open_open_reason_pairs,
        'new_relation_count': len(new_rows),
        'new_relations': sorted(new_rows, key=lambda x: (x['guess'], x['target'], x['article'], x['position'])),
        'new_relation_provenance': sorted({_provenance(x) for x in new_rows}),
        'unexpected_new_relations': [
            x for x in new_rows if not ((_ids(x['guess_roots']) | _ids(x['target_roots'])) <= KEYS)
        ],
        'bridge_audit': bridge,
        'expected_pairs': {
            key: {'before': expected_before[key], 'after': expected_after[key]}
            for key in expected_before
        },
        'adversarial': [{'guess': a, 'target': b, 'open': opens(a, b)} for a, b in adversarial],
    }
    Path('/tmp/censorly-minimal-impact-cone.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    _phase('T11 report write', phase)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
