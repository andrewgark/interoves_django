"""Lemma frequencies of a representative Censorly article sample.

Offline prioritisation only; gameplay never reads it. The committed
``lexical/data/corpus_lemma_freq.tsv.gz`` comes from
``offline/tools/corpus/build_corpus.py`` (pool articles fetched and
tokenized exactly like a puzzle). Without that file, the tiny test fixture
corpus is used so the tools keep working.

Counts exclude initially open service words: those never need a root.
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / 'lexical' / 'data'
FREQ_PATH = DATA_DIR / 'corpus_lemma_freq.tsv.gz'
META_PATH = DATA_DIR / 'corpus_lemma_freq.meta.json'


@dataclass
class Corpus:
    source: str
    articles: int
    service_tokens: int
    tokens: Counter = field(default_factory=Counter)        # lemma -> occurrences
    documents: Counter = field(default_factory=Counter)     # lemma -> articles
    surfaces: dict = field(default_factory=dict)            # lemma -> most frequent surface

    @property
    def content_tokens(self) -> int:
        return sum(self.tokens.values())


def _from_file() -> Corpus:
    meta = json.loads(META_PATH.read_text(encoding='utf-8')) if META_PATH.exists() else {}
    corpus = Corpus(
        source=f'{FREQ_PATH.name} ({meta.get("articles", "?")} pool articles)',
        articles=int(meta.get('articles') or 0),
        service_tokens=int(meta.get('service_tokens') or 0),
    )
    with gzip.open(FREQ_PATH, 'rt', encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            corpus.tokens[row['lemma']] = int(row['tokens'])
            corpus.documents[row['lemma']] = int(row['articles'])
            corpus.surfaces[row['lemma']] = row['top_surface']
    return corpus


def _from_fixtures() -> Corpus:
    from games.puzzles.censorly.lexical.match.core import fold
    from games.puzzles.censorly.lexical.semantics import initially_open
    from games.puzzles.censorly.offline.audit.prelaunch import iter_fixtures
    from games.puzzles.censorly.text.tokenize import tokenize_text

    corpus = Corpus(source='test fixtures (no corpus_lemma_freq.tsv.gz)', articles=0, service_tokens=0)
    for _name, _kind, body in iter_fixtures():
        corpus.articles += 1
        seen = set()
        for token in tokenize_text(body):
            surface = token.get('surface') or ''
            if token.get('kind') != 'content' or not surface:
                continue
            if initially_open(surface):
                corpus.service_tokens += 1
                continue
            lemma = fold(token.get('lemma') or surface)
            corpus.tokens[lemma] += 1
            corpus.surfaces.setdefault(lemma, fold(surface))
            seen.add(lemma)
        corpus.documents.update(seen)
    return corpus


@lru_cache(maxsize=1)
def load_corpus() -> Corpus:
    return _from_file() if FREQ_PATH.exists() else _from_fixtures()
