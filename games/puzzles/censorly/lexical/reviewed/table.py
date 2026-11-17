"""``ReviewedTable``: one hand-maintained (or reviewed-generator) TSV.

A table knows its file, its exact header and how to turn rows into the
lookup the deciders use. Loading validates every row and raises on a
malformed file, so a bad edit fails at import instead of silently
changing gameplay.
"""

from __future__ import annotations

import csv
import re
from functools import cached_property
from pathlib import Path
from threading import Lock

from games.puzzles.censorly.lexical.core import fold

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'

# ``+`` separates components of a complete structure. ``|`` belongs to
# Kuznetsova family ids (fam:ветер|ветр) and is not a separator.
ROOT_ID_RE = re.compile(r'^(?:fam|tikh|sense|root):[^+\s]+$')


class ReviewedTable:
    """A TSV under ``lexical/data`` with a fixed header."""

    def __init__(self, filename: str, columns: tuple[str, ...]):
        self.path = DATA_DIR / filename
        self.columns = columns
        self._lock = Lock()

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            raise RuntimeError(f'missing reviewed data file: {self.path}')
        with self.path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream, delimiter='\t')
            if tuple(reader.fieldnames or ()) != self.columns:
                raise RuntimeError(f'invalid header in {self.path}: expected {self.columns}')
            return [
                {key: (value or '').strip() for key, value in row.items()}
                for row in reader
            ]

    @cached_property
    def data(self):
        with self._lock:
            return self._build(self.rows())

    def _build(self, rows):
        return rows

    def fail(self, message: str):
        raise RuntimeError(f'{self.path.name}: {message}')


def parse_structure(raw: str, *, allow_sense: bool = False) -> tuple[str, ...]:
    """``fam:a+root:b`` → sorted root ids; ``None`` when malformed."""
    parts = tuple(sorted(item.strip() for item in raw.split('+')))
    if not parts or any(not ROOT_ID_RE.fullmatch(item) for item in parts):
        return None
    if not allow_sense and any(item.startswith('sense:') for item in parts):
        return None
    return parts


class SpellingSet(ReviewedTable):
    """A set of folded spellings from one key column."""

    def __init__(self, filename, columns, key):
        super().__init__(filename, columns)
        self.key = key

    def _build(self, rows) -> frozenset[str]:
        found = set()
        for row in rows:
            spelling = fold(row[self.key])
            if not spelling:
                self.fail('empty spelling')
            if spelling in found:
                self.fail(f'duplicate spelling: {spelling}')
            found.add(spelling)
        return frozenset(found)


class StructureTable(ReviewedTable):
    """lemma → complete root structures, written as ``fam:a+root:b``.

    ``alternatives`` allows several different structures per lemma (each a
    separate row); otherwise a lemma has exactly one.
    """

    def __init__(self, filename, *, alternatives: bool):
        super().__init__(filename, ('lemma', 'root_structure', 'note'))
        self.alternatives = alternatives

    def _build(self, rows) -> dict[str, tuple[tuple[str, ...], ...]]:
        found: dict[str, list[tuple[str, ...]]] = {}
        for row in rows:
            lemma = fold(row['lemma'])
            if not lemma or not row['root_structure']:
                self.fail(f'empty assignment for {lemma!r}')
            structure = parse_structure(row['root_structure'])
            if structure is None:
                self.fail(f'invalid root structure {row["root_structure"]!r} for {lemma}')
            existing = found.setdefault(lemma, [])
            if structure in existing:
                if self.alternatives:
                    self.fail(f'duplicate assignment: {lemma} {row["root_structure"]}')
                continue
            if existing and not self.alternatives:
                self.fail(f'conflicting assignment: {lemma}')
            existing.append(structure)
        return {lemma: tuple(items) for lemma, items in found.items()}


class PairSet(ReviewedTable):
    """(Tikhonov morph, game root) pairs, e.g. rejected spelling matches."""

    def _build(self, rows) -> frozenset[tuple[str, str]]:
        found = set()
        for row in rows:
            morph, root = fold(row['morph']), row['root']
            if not morph or not ROOT_ID_RE.fullmatch(root):
                self.fail(f'invalid row: {row}')
            if (morph, root) in found:
                self.fail(f'duplicate pair: {morph} {root}')
            found.add((morph, root))
        return frozenset(found)


class ComponentTable(ReviewedTable):
    """Tikhonov morph → root id it takes inside a compound only."""

    def _build(self, rows) -> dict[str, str]:
        found = {}
        for row in rows:
            morph, root = fold(row['morph']), row['root']
            if not morph or not ROOT_ID_RE.fullmatch(root) or root.startswith('sense:'):
                self.fail(f'invalid row: {row}')
            if morph in found:
                self.fail(f'duplicate morph: {morph}')
            found[morph] = root
        return found


class CompoundHeadTable(ReviewedTable):
    """Compound lemma → the lemma of its meaningful part (телесериал → сериал)."""

    def _build(self, rows) -> dict[str, str]:
        found = {}
        for row in rows:
            compound, head = fold(row['compound']), fold(row['head'])
            if not compound or not head or head == compound:
                self.fail(f'invalid row: {row}')
            if compound in found:
                self.fail(f'duplicate compound: {compound}')
            found[compound] = head
        return found


class AnchorTable(ReviewedTable):
    """lemma → anchor lemma whose root structures the lemma copies.

    ``source`` names the provenance the root bank records. ``replace`` says
    when the copy wins over the lemma's own structures:

    * ``rootless``: only a lemma without structures takes the anchor's;
    * ``bare_subset``: also a lemma whose own structures carry no sense and
      whose roots are a subset of the anchor's (не- negations);
    * ``always``: the copy replaces whatever the lemma had (пол- compounds).

    ``check(lemma, anchor)`` returns an error message for a malformed row.
    """

    def __init__(
        self, filename, columns, *, source, replace, check=None,
        lemma_column=None, anchor_column=None, allow_empty_anchor=False,
    ):
        super().__init__(filename, columns)
        self.source = source
        self.replace = replace
        self.check = check
        self.lemma_column = lemma_column or columns[0]
        self.anchor_column = anchor_column or columns[1]
        self.allow_empty_anchor = allow_empty_anchor

    def _build(self, rows) -> dict[str, str]:
        found = {}
        for row in rows:
            lemma, anchor = fold(row[self.lemma_column]), fold(row[self.anchor_column])
            if not lemma or lemma == anchor or not (anchor or self.allow_empty_anchor):
                self.fail(f'invalid anchor: {lemma!r} -> {anchor!r}')
            problem = self.check(lemma, anchor) if self.check else ''
            if problem:
                self.fail(problem)
            if lemma in found:
                self.fail(f'duplicate lemma: {lemma}')
            found[lemma] = anchor
        return found


class GroupTable(ReviewedTable):
    """Rows of space-separated words; each row is one group."""

    def __init__(self, filename, columns, key):
        super().__init__(filename, columns)
        self.key = key

    def _build(self, rows) -> tuple[tuple[str, ...], ...]:
        groups = []
        for row in rows:
            words = tuple(fold(word) for word in row[self.key].split())
            if len(words) < 2:
                self.fail(f'group needs two words: {row[self.key]!r}')
            groups.append(words)
        return tuple(groups)


class SenseRuleTable(ReviewedTable):
    """Ordered ``(family, pattern, sense)`` rules for whole dictionary families.

    Rows of one Kuznetsova family form an ordered list; the first pattern
    that ``re.fullmatch``-es the lemma decides its sense (``-``: none). The
    last rule of every family is ``.*`` so each family lemma gets a decision.
    """

    SENSE_RE = re.compile(r'^sense:\S+$')

    def _build(self, rows) -> dict[str, tuple[tuple[re.Pattern, str | None], ...]]:
        rules: dict[str, list[tuple[re.Pattern, str | None]]] = {}
        for row in rows:
            family, pattern, sense = row['family'], row['pattern'], row['sense']
            if not family or not pattern:
                self.fail(f'invalid row: {row}')
            if sense != '-' and not self.SENSE_RE.fullmatch(sense):
                self.fail(f'invalid sense {sense!r} for {family}')
            try:
                compiled = re.compile(pattern)
            except re.error as error:
                self.fail(f'invalid pattern {pattern!r} for {family}: {error}')
            family_rules = rules.setdefault(family, [])
            if family_rules and family_rules[-1][0].pattern == '.*':
                self.fail(f'unreachable rule after .* in {family}: {pattern}')
            family_rules.append((compiled, None if sense == '-' else sense))
        for family, family_rules in rules.items():
            if family_rules[-1][0].pattern != '.*':
                self.fail(f'family {family} does not end with a .* rule')
        return {family: tuple(family_rules) for family, family_rules in rules.items()}

    def classify(self, family: str, lemma: str) -> str | None:
        for pattern, sense in self.data[family]:
            if pattern.fullmatch(lemma):
                return sense
        raise AssertionError('unreachable: every family ends with .*')


class SenseRootTable(ReviewedTable):
    """sense → the one root every reading of that sense is built on."""

    def _build(self, rows) -> dict[str, str]:
        found: dict[str, str] = {}
        for row in rows:
            sense, root = row['sense'], row['root']
            if not sense.startswith('sense:') or not ROOT_ID_RE.fullmatch(root) or root.startswith('sense:'):
                self.fail(f'invalid row: {row}')
            if sense in found:
                self.fail(f'duplicate sense: {sense}')
            found[sense] = root
        return found


class SenseAssignmentTable(ReviewedTable):
    """lemma → its sense readings, overriding ``SenseRuleTable``.

    ``senses`` is ``|``-separated (several readings of one spelling) or
    ``-`` (no sense even where a family rule would give one).
    """

    NAME_RE = re.compile(r'^(?:sense|root|fam):[^\s|+]+$')

    def _build(self, rows) -> dict[str, tuple[str, ...]]:
        found: dict[str, tuple[str, ...]] = {}
        for row in rows:
            lemma, raw = fold(row['lemma']), row['senses']
            if not lemma or not raw:
                self.fail(f'invalid row: {row}')
            names = () if raw == '-' else tuple(raw.split('|'))
            if any(not self.NAME_RE.fullmatch(name) for name in names) or len(set(names)) != len(names):
                self.fail(f'invalid senses {raw!r} for {lemma}')
            if lemma in found:
                self.fail(f'duplicate lemma: {lemma}')
            found[lemma] = names
        return found
