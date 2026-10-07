"""Compact approved-edge index.

The file stores one undirected edge per line. At runtime it becomes an
adjacency map of lemma → neighbours. There is no root-family clique and
no matrix of all pairs.
"""

from __future__ import annotations

import gzip
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / 'data' / 'ru_graph.tsv.gz'
_neighbors: dict[str, tuple[str, ...]] | None = None
_proofs: dict[frozenset[str], str] | None = None


def neighbors(lemma: str) -> tuple[str, ...]:
    _load()
    return _neighbors.get(lemma, ()) if _neighbors else ()


def proof_between(left: str, right: str) -> str:
    _load()
    if not _proofs:
        return ''
    return _proofs.get(frozenset((left, right)), '')


def edge_count() -> int:
    _load()
    return len(_proofs or ())


def _load() -> None:
    global _neighbors, _proofs
    if _neighbors is not None:
        return
    adjacency: dict[str, list[str]] = {}
    proofs: dict[frozenset[str], str] = {}
    if _PATH.is_file():
        with gzip.open(_PATH, 'rt', encoding='utf-8') as handle:
            for line in handle:
                left, right, proof = line.rstrip('\n').split('\t')
                adjacency.setdefault(left, []).append(right)
                adjacency.setdefault(right, []).append(left)
                proofs[frozenset((left, right))] = proof
    _neighbors = {lemma: tuple(items) for lemma, items in adjacency.items()}
    _proofs = proofs


def reset_cache() -> None:
    global _neighbors, _proofs
    _neighbors = None
    _proofs = None
