"""Lemma frequencies of a representative Censorly article sample.

Offline prioritisation only; gameplay never reads it. The committed
``lexical/data/corpus_lemma_freq.tsv.gz`` comes from
``offline/tools/corpus/build_corpus.py`` (pool articles fetched and
tokenized exactly like a puzzle). Without that file, the tiny test fixture
corpus is used so the tools keep working.

Counts exclude initially open service words: those never need a root.

``corpus_holdout_freq.tsv.gz`` is a second sample of pool articles that are
not in the main corpus. No queue ranks by it: it only measures whether
coverage gains carry over to articles the review never looked at
(``coverage_report --holdout``).
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
HOLDOUT_FREQ_PATH = DATA_DIR / 'corpus_holdout_freq.tsv.gz'
HOLDOUT_META_PATH = DATA_DIR / 'corpus_holdout_freq.meta.json'


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


def _from_file(freq_path: Path = FREQ_PATH, meta_path: Path = META_PATH) -> Corpus:
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    corpus = Corpus(
        source=f'{freq_path.name} ({meta.get("articles", "?")} pool articles)',
        articles=int(meta.get('articles') or 0),
        service_tokens=int(meta.get('service_tokens') or 0),
    )
    with gzip.open(freq_path, 'rt', encoding='utf-8', newline='') as stream:
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


@lru_cache(maxsize=2)
def load_corpus(holdout: bool = False) -> Corpus:
    if holdout:
        if not HOLDOUT_FREQ_PATH.exists():
            raise FileNotFoundError(
                f'{HOLDOUT_FREQ_PATH.name} is missing; build it with build_corpus --holdout',
            )
        return _from_file(HOLDOUT_FREQ_PATH, HOLDOUT_META_PATH)
    return _from_file() if FREQ_PATH.exists() else _from_fixtures()
