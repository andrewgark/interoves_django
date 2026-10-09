"""Cold-start profiler for the minimal impact-cone audit.

Diagnostic only: it does not build a target index, walk pymorphy words, or
change resolver configuration.
"""
from __future__ import annotations

import gc
import importlib
import os
import resource
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')


def mark(name, started, rss0=None, note=''):
    now = time.perf_counter()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    delta = '' if rss0 is None else f' rss_delta_mb={rss - rss0:.1f}'
    print(f'{name} seconds={now - started:.3f} rss_mb={rss:.1f}{delta} {note}', flush=True)
    return now, rss


print(f'T0 process start seconds=0.000 rss_mb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f}', flush=True)

t = time.perf_counter()
import django
django.setup()
t, r = mark('T1 Django/setup', t)

t = time.perf_counter()
rootbank = importlib.import_module('games.censorly.lexical.rootbank')
t, r = mark('T2 rootbank import', t, r)

t = time.perf_counter()
semantics = importlib.import_module('games.censorly.lexical.semantics')
t, r = mark('T2b semantics import', t, r)

t = time.perf_counter()
match = importlib.import_module('games.censorly.lexical.match')
t, r = mark('T2c match import', t, r)

t = time.perf_counter()
before = r
bank, load_seconds = rootbank._bank()
t, r = mark('T3 rootbank initialization/load', t, before, f'bank_lemmas={len(bank)} reported_load_seconds={load_seconds:.3f}')

t = time.perf_counter()
from games.matcher.norm_matcher import MORPH_ANALYZER
t, r = mark('T4 pymorphy analyzer ready', t, r, f'analyzer={type(MORPH_ANALYZER).__name__}')

t = time.perf_counter()
opaque = [lemma for lemma, structures in bank.items() if ('tikh:сенат',) in structures]
ivolg = [lemma for lemma, structures in bank.items() if any('fam:иволг' in item for item in structures)]
t, r = mark('T5 affected lemma enumeration', t, r, f'opaque={len(opaque)} ivolg={len(ivolg)}')

t = time.perf_counter()
forms = set()
for lemma in sorted(set(opaque + ivolg + ['иволговый'])):
    for parse in MORPH_ANALYZER.parse(lemma):
        forms.update(item.word.casefold().replace('ё', 'е') for item in parse.lexeme)
    forms.add(lemma)
t, r = mark('T6 lexeme form generation', t, r, f'forms={len(forms)}')

t = time.perf_counter()
from games.censorly.lexical.prelaunch import iter_fixtures
t, r = mark('T7 fixture discovery', t, r)

t = time.perf_counter()
from games.censorly.tokenize import tokenize_text
fixture_count = 0
token_count = 0
for _rel, _kind, body in iter_fixtures():
    fixture_count += 1
    token_count += len(tokenize_text(body))
t, r = mark('T8 fixture tokenization', t, r, f'fixtures={fixture_count} tokens={token_count}')

t = time.perf_counter()
affected = []
for rel, _kind, body in iter_fixtures():
    for token in tokenize_text(body):
        surface = token.get('surface') or ''
        if surface.casefold().replace('ё', 'е') in forms:
            affected.append((rel, token.get('id'), surface))
t, r = mark('T9 affected surface filtering', t, r, f'occurrences={len(affected)}')

t = time.perf_counter()
packs = [match._GuessPack(surface) for _rel, _pos, surface in affected]
t, r = mark('T10 runtime checks', t, r, f'packs={len(packs)}')

t = time.perf_counter()
del packs
gc.collect()
t, r = mark('T11 report write', t, r, 'diagnostic complete')
print('COLD_START_BOTTLENECK_PROFILE_COMPLETE', flush=True)

