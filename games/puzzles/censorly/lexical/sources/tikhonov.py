"""Tikhonov morpheme dictionary (RuMorphs): lemma → root morphs."""

from __future__ import annotations

from dataclasses import dataclass

from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.sources.base import (
    DICTIONARY_DIR,
    ExternalDictionary,
    lines,
    plain,
    strip_note,
)


@dataclass(frozen=True)
class TikhonovData:
    # lemma → root morph spellings of its last segmentation row.
    roots_of_lemma: dict[str, tuple[str, ...]]
    # lemma → root morphs of every row, for diagnostics.
    all_rows_of_lemma: dict[str, tuple[str, ...]]


class Tikhonov(ExternalDictionary):
    name = 'tikhonov'

    def __init__(self):
        super().__init__(DICTIONARY_DIR / 'RuMorphs-Lemmas.txt')

    def _load(self) -> TikhonovData:
        last: dict[str, tuple[str, ...]] = {}
        every: dict[str, list[str]] = {}
        for line in lines(self.paths[0]):
            raw_lemma, segmentation = line.split('\t', 1)
            lemma = fold(strip_note(raw_lemma))
            roots = []
            for part in segmentation.split('/'):
                if ':' not in part:
                    continue
                morph, kind = part.rsplit(':', 1)
                if kind == 'ROOT':
                    spelled = plain(morph)
                    if spelled:
                        roots.append(spelled)
            if lemma and roots:
                last[lemma] = tuple(roots)
                every.setdefault(lemma, []).extend(roots)
        return TikhonovData(
            roots_of_lemma=last,
            all_rows_of_lemma={lemma: tuple(items) for lemma, items in every.items()},
        )

    def lemmas(self):
        return self.data.roots_of_lemma.keys()

    def roots_of(self, lemma: str) -> tuple[str, ...]:
        return self.data.roots_of_lemma.get(lemma, ())

    def all_rows_of(self, lemma: str) -> tuple[str, ...]:
        return self.data.all_rows_of_lemma.get(lemma, ())

    def lemmas_by_root(self) -> dict[str, set[str]]:
        """Root morph spelling → every lemma with that root in some row."""
        found: dict[str, set[str]] = {}
        for lemma, roots in self.data.all_rows_of_lemma.items():
            for root in roots:
                found.setdefault(root, set()).add(lemma)
        return found


TIKHONOV = Tikhonov()
