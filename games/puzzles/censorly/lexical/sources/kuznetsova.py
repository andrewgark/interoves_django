"""Kuznetsova–Efremova root dictionary: lemma → root, root → allomorph family."""

from __future__ import annotations

from dataclasses import dataclass

from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.sources.base import (
    DICTIONARY_DIR,
    ExternalDictionary,
    lines,
    norm,
    plain,
    strip_note,
)


@dataclass(frozen=True)
class KuznetsovaData:
    # numbered morph (вод¹) → family id (вод¹|вож); superscripts are kept.
    family_of_morph: dict[str, str]
    # plain spelling (вод) → [(numbered morph, family)] in file order.
    morphs_by_spelling: dict[str, list[tuple[str, str]]]
    # lemma → numbered roots in file order.
    roots_of_lemma: dict[str, tuple[str, ...]]
    # lemma → family ids in file order.
    families_of_lemma: dict[str, tuple[str, ...]]
    # every (lemma, numbered root) line in file order, repeats included.
    lines: tuple[tuple[str, str], ...]


class Kuznetsova(ExternalDictionary):
    name = 'kuznetsova'

    def __init__(self):
        super().__init__(
            DICTIONARY_DIR / 'lemmas_to_roots.tsv',
            DICTIONARY_DIR / 'root_groups.txt',
        )

    @property
    def lemmas_path(self):
        return self.paths[0]

    @property
    def groups_path(self):
        return self.paths[1]

    def _load(self) -> KuznetsovaData:
        family_of_morph = read_families(self.groups_path)
        morphs_by_spelling: dict[str, list[tuple[str, str]]] = {}
        for morph, family in family_of_morph.items():
            morphs_by_spelling.setdefault(plain(morph), []).append((morph, family))
        roots: dict[str, list[str]] = {}
        families: dict[str, list[str]] = {}
        rows: list[tuple[str, str]] = []
        for line in lines(self.lemmas_path):
            raw_lemma, raw_root = line.split('\t', 1)
            lemma = fold(strip_note(raw_lemma))
            numbered = norm(raw_root.strip())
            if not lemma or not numbered:
                continue
            rows.append((lemma, numbered))
            lemma_roots = roots.setdefault(lemma, [])
            if numbered not in lemma_roots:
                lemma_roots.append(numbered)
            family = family_of_morph.get(numbered, numbered)
            lemma_families = families.setdefault(lemma, [])
            if family not in lemma_families:
                lemma_families.append(family)
        return KuznetsovaData(
            family_of_morph=family_of_morph,
            morphs_by_spelling=morphs_by_spelling,
            roots_of_lemma={lemma: tuple(items) for lemma, items in roots.items()},
            families_of_lemma={lemma: tuple(items) for lemma, items in families.items()},
            lines=tuple(rows),
        )

    def lemmas(self):
        return self.data.roots_of_lemma.keys()

    def rows(self):
        """(lemma, numbered root, family) for every dictionary line, in file order."""
        for lemma, numbered in self.data.lines:
            yield lemma, numbered, self.family_of(numbered)

    def roots_of(self, lemma: str) -> tuple[str, ...]:
        """Numbered roots of a lemma (вод¹), empty when the lemma is absent."""
        return self.data.roots_of_lemma.get(lemma, ())

    def families_of(self, lemma: str) -> tuple[str, ...]:
        return self.data.families_of_lemma.get(lemma, ())

    def family_of(self, numbered: str) -> str:
        """Family id of a numbered morph; an ungrouped morph is its own family."""
        return self.data.family_of_morph.get(numbered, numbered)

    def morphs_spelled(self, morph: str) -> list[tuple[str, str]]:
        """Every (numbered morph, family) whose spelling equals this morph."""
        hits = self.data.morphs_by_spelling.get(plain(morph))
        if hits:
            return hits
        numbered = norm(morph)
        family = self.data.family_of_morph.get(numbered)
        return [(numbered, family)] if family else []

    def has_spelling(self, spelling: str) -> bool:
        return bool(self.data.morphs_by_spelling.get(spelling))


def read_families(path) -> dict[str, str]:
    """``root_groups.txt``: each line is one allomorph family, ``a → b`` redirects."""
    redirect: dict[str, str] = {}
    morph_to_id: dict[str, str] = {}
    for line in lines(path):
        if '→' in line:
            left, right = (norm(part.strip()) for part in line.split('→', 1))
            if left and right:
                redirect[left] = right
            continue
        parts = []
        for part in line.split('/'):
            part_n = norm(part.strip())
            if part_n and part_n not in parts:
                parts.append(part_n)
        if not parts:
            continue
        family = '|'.join(parts)
        for part in parts:
            morph_to_id[part] = family
    resolved = {}
    for morph, family in morph_to_id.items():
        seen: set[str] = set()
        current = morph
        while current in redirect and current not in seen:
            seen.add(current)
            current = redirect[current]
        resolved[morph] = morph_to_id.get(current, family)
    return resolved


KUZNETSOVA = Kuznetsova()
