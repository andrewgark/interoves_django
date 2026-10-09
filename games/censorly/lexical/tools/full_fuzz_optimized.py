"""Cached full-fuzz runner.

This is an offline audit tool.  It reuses the existing family-audit lookup and
resolver code; the only additional state is a pickle containing the already
enumerated pymorphy universe, so later runs do not walk the dictionary again.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import resource
import time
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical import family_audit as audit
from games.censorly.lexical.semantics import readings_of


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _universe(path: Path):
    started = time.perf_counter()
    if path.exists():
        with path.open('rb') as stream:
            value = pickle.load(stream)
        return value, 'cache', time.perf_counter() - started

    value = audit.walk_dictionary()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        pickle.dump(value, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return value, 'built', time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=Path('/tmp/censorly-full-universe.pkl'))
    parser.add_argument('--output', type=Path, default=Path('/tmp/censorly-full-fuzz-optimized.json'))
    args = parser.parse_args()

    universe, universe_source, universe_seconds = _universe(args.cache)
    pymorphy_lemmas, ambiguous = universe

    # Prevent run_fuzz from walking the dictionary again.  Its candidate-set
    # construction remains byte-for-byte the existing audit implementation.
    audit.walk_dictionary = lambda: universe

    corpus_started = time.perf_counter()
    corpus = audit.load_corpus()
    corpus_seconds = time.perf_counter() - corpus_started
    grouped, _multi, _inventory = audit.build_inventory()

    before_reads = readings_of.cache_info()
    started = time.perf_counter()
    fuzz = audit.run_fuzz(corpus, grouped)
    fuzz_seconds = time.perf_counter() - started
    after_reads = readings_of.cache_info()

    result = {
        'universe_source': universe_source,
        'universe_cache': str(args.cache),
        'universe_seconds': round(universe_seconds, 3),
        'corpus_seconds': round(corpus_seconds, 3),
        'target_index_and_fuzz_seconds': round(fuzz_seconds, 3),
        'fuzz_seconds_reported': fuzz['seconds'],
        'unique_guesses_analyzed': fuzz['guesses'],
        'pymorphy_lemmas': len(pymorphy_lemmas),
        'ambiguous_forms': len(ambiguous),
        'target_occurrences': fuzz['occurrences'],
        'morphology_cache_before': before_reads._asdict(),
        'morphology_cache_after': after_reads._asdict(),
        'morphology_calls_delta': after_reads.misses - before_reads.misses,
        'guesses_with_opening': fuzz['opened_guesses'],
        'opening_rows': fuzz['opening_rows'],
        'max_occurrence_fanout': max(fuzz['fanout_occ'] or [0]),
        'max_lemma_fanout': max(fuzz['fanout_lemmas'] or [0]),
        'p50_lemma_fanout': sorted(fuzz['fanout_lemmas'] or [0])[int(.50 * (len(fuzz['fanout_lemmas'] or [0]) - 1))],
        'p95_lemma_fanout': sorted(fuzz['fanout_lemmas'] or [0])[int(.95 * (len(fuzz['fanout_lemmas'] or [0]) - 1))],
        'p99_lemma_fanout': sorted(fuzz['fanout_lemmas'] or [0])[int(.99 * (len(fuzz['fanout_lemmas'] or [0]) - 1))],
        'fanout_ge_50': fuzz['ge'][50],
        'root_openings': dict(fuzz['root_openings']),
        'max_rss_mb': round(_rss_mb(), 1),
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
