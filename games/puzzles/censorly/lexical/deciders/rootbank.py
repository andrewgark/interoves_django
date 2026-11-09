"""``RootBank``: lemma → complete root structures.

A structure is a sorted tuple of root ids. Sorting drops order and keeps
repeats, so ``белоснежный`` matches ``снежно-белый`` and ``один-одинехонек``
does not match ``один``. A word never stores a partial component list: if
one required component has no authorized reading, the whole lemma is
unresolved and cannot match by an empty root. ``sense:*`` is metadata
appended to a complete root structure and is never a root by itself.

Order of evidence for one lemma, first match wins:

1. a sense assignment (``splits.py``, ``proper_names.py``): the root side
   comes from ``SEMANTIC_ROOT_ASSIGNMENTS`` or the dictionaries;
2. ``reviewed_derived_roots.tsv``, 3. ``reviewed_existing_root_assignments.tsv``;
4. a Tikhonov compound, each morph authorized separately; a morph that
   stays unauthorized may take its ``compound_components.tsv`` id;
5. Kuznetsova; 6. one Tikhonov morph: reviewed opaque root, else a unique
   spelling match that is not rejected, else unresolved.

Then anchor tables copy structures between lemmas (``AnchorTable``) and
digits take the structures of their cardinal numerals.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.sources import KUZNETSOVA, TIKHONOV
from games.puzzles.censorly.lexical.sources.base import plain

# Two historical families whose dictionary join glues unrelated modern words.
# чит/чт/ч are one reading root; чет stays apart. голова and глава are one
# historical root and two modern words.
_READ_MORPHS = frozenset({'чит', 'чт', 'ч'})
_HEAD_FAMILY = frozenset({'глав', 'голав', 'голов'})
_TOO_MANY_COMBINATIONS = 32


@lru_cache(maxsize=None)
def game_root_id(numbered: str, family: str) -> str:
    """Game root id of a numbered Kuznetsova morph in its family."""
    spellings = {plain(part) for part in family.split('|')}
    if 'чит' in spellings and 'чет' in spellings:
        spelled = plain(numbered)
        if spelled in _READ_MORPHS or numbered in _READ_MORPHS:
            return 'root:read'
        return 'root:' + spelled
    if spellings == _HEAD_FAMILY:
        return 'root:head' if plain(numbered) != 'глав' else 'root:chief'
    return 'fam:' + family


@dataclass
class Assignment:
    source: str
    detail: str = ''
    refused: tuple[str, ...] = ()


@dataclass
class BuiltBank:
    structures: dict[str, tuple[tuple[str, ...], ...]]
    families: dict[str, tuple[str, ...]]
    seconds: float
    provenance: dict[str, dict] = field(default_factory=dict)
    tikhonov_rows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    audit: dict[str, int] = field(default_factory=dict)


class _DiscardWrites(dict):
    """Provenance sink for gameplay builds: assignments are not recorded."""

    def __setitem__(self, key, value):
        pass


class RootBank:
    """Builds and answers ``structures_of``. Built once per process."""

    # Anchor tables in application order; negations run twice so that
    # «неверно» can anchor to «неверный» and «необычно» follow «обычно».
    def anchor_passes(self):
        return (
            (reviewed.NEGATIONS, 'bare_subset'),
            (reviewed.NEST_ANCHORS, 'rootless'),
            (reviewed.ADVERB_BASES, 'rootless'),
            (reviewed.NEGATIONS, 'rootless'),
            (reviewed.HALF_COMPOUNDS, 'always'),
        )

    def structures_of(self, lemma: str) -> tuple[tuple[str, ...], ...]:
        return self.built().structures.get(fold(lemma), ())

    def families_of(self, lemma: str) -> tuple[str, ...]:
        return self.built().families.get(fold(lemma), ())

    def lemmas(self):
        """Every lemma that has structures."""
        return self.built().structures.keys()

    def provenance(self) -> dict[str, dict]:
        """Per-lemma source, detail and refused candidates (diagnostic build)."""
        return self.diagnostics().provenance

    def assignment_of(self, lemma: str) -> dict:
        """Where a lemma's structures came from; ``ABSENT`` when no source lists it.

        ``refused`` lists game roots that share a Tikhonov spelling but were
        not assigned.
        """
        folded = fold(lemma)
        info = self.provenance().get(folded)
        if info is None:
            return {'structures': (), 'source': 'ABSENT', 'detail': '', 'refused': ()}
        return {
            'structures': self.structures_of(folded),
            'source': info['source'],
            'detail': info['detail'],
            'refused': info['refused'],
        }

    def tikhonov_roots_of(self, lemma: str) -> tuple[str, ...]:
        """Raw Tikhonov root morphs of every segmentation row, for diagnostics."""
        return self.diagnostics().tikhonov_rows.get(fold(lemma), ())

    def component_audit(self) -> dict[str, int]:
        return dict(self.built().audit)

    def load_seconds(self) -> float:
        return self.built().seconds

    @lru_cache(maxsize=1)
    def built(self) -> BuiltBank:
        bank = self.build(record=False)
        # Gameplay keeps only the bank; raw dictionaries are reloaded on demand.
        KUZNETSOVA.release()
        TIKHONOV.release()
        return bank

    @lru_cache(maxsize=1)
    def diagnostics(self) -> BuiltBank:
        """A second build that records provenance (~40 MB); tools and tests only."""
        bank = self.build(record=True)
        KUZNETSOVA.release()
        TIKHONOV.release()
        return bank

    def reset(self) -> None:
        self.built.cache_clear()
        self.diagnostics.cache_clear()

    # ── build ──────────────────────────────────────────────────────────────

    def build(self, *, record: bool) -> BuiltBank:
        from games.puzzles.censorly.lexical.senses import SEMANTIC_ROOT_ASSIGNMENTS, SENSES

        started = time.perf_counter()
        audit: Counter[str] = Counter()
        built: dict[str, tuple[tuple[str, ...], ...]] = {}
        provenance: dict[str, dict] = {} if record else _DiscardWrites()
        derived = reviewed.DERIVED_ROOTS.data
        existing = reviewed.EXISTING_ROOTS.data

        def assign(lemma, structures, info: Assignment):
            if structures is not None:
                built[lemma] = structures
            provenance[lemma] = {
                'source': info.source, 'detail': info.detail, 'refused': info.refused,
            }

        lemmas = set(KUZNETSOVA.lemmas()) | set(TIKHONOV.lemmas()) | set(SENSES) | set(derived)
        for lemma in lemmas:
            parts = TIKHONOV.roots_of(lemma)
            senses = SENSES.get(lemma) or ()
            kuz_ids = self.kuznetsova_ids(lemma)
            shape = 'multi' if len(parts) >= 2 else 'single'
            if parts:
                audit[f'tikhonov_lemmas_{shape}'] += 1
            for part in parts:
                count = len(self.candidates(part))
                audit['components_total'] += 1
                audit[
                    'ambiguous_spelling_components' if count > 1
                    else 'unique_candidate_components' if count == 1
                    else 'zero_candidate_components'
                ] += 1

            if senses:
                self._assign_senses(
                    lemma, senses, parts, kuz_ids, SEMANTIC_ROOT_ASSIGNMENTS, assign, audit,
                )
                continue
            if lemma in derived:
                assign(lemma, derived[lemma], Assignment(
                    'REVIEWED_DERIVED_ROOT', ','.join('|'.join(item) for item in derived[lemma]),
                ))
                audit['reviewed_derived_lemmas'] += 1
                continue
            if lemma in existing:
                assign(lemma, existing[lemma], Assignment(
                    'REVIEWED_EXISTING_ROOT', ','.join('|'.join(item) for item in existing[lemma]),
                ))
                audit['reviewed_existing_lemmas'] += 1
                continue
            if len(parts) >= 2:
                structures, refused, source, rows = self.explode(parts, set(kuz_ids))
                for row in rows:
                    if row['kind'] == 'UNIQUE_SPELLING_FALLBACK':
                        audit['unique_spelling_fallback_components'] += 1
                    elif row['n_cand'] > 1 and row['n_auth']:
                        audit['ambiguous_resolved_by_kuz'] += 1
                    if row['n_auth'] > 1:
                        audit['legitimate_multiple_components'] += 1
                    if row['kind'] == 'UNRESOLVED':
                        audit['unresolved_components'] += 1
                if structures:
                    assign(lemma, structures, Assignment(source, '+'.join(parts), refused))
                    if len(structures) > 1:
                        audit['legitimate_multiple_lemmas'] += 1
                else:
                    assign(lemma, None, Assignment(source, source + ' ' + '+'.join(parts), refused))
                    audit['whole_lemmas_unresolved'] += 1
                    audit[f'unresolved_lemmas_{shape}'] += 1
                    if source == 'UNRESOLVED_TOO_AMBIGUOUS':
                        audit['too_ambiguous_lemmas'] += 1
                continue
            ids = list(kuz_ids)
            source = 'KUZNETSOVA_MULTI' if len(ids) > 1 else 'KUZNETSOVA'
            if not ids and len(parts) == 1:
                authorized, kind, extra = self.authorize(parts[0], ())
                if not authorized:
                    assign(lemma, None, Assignment(
                        'UNRESOLVED', f'morph {parts[0]} matches {len(extra)} game roots', extra,
                    ))
                    audit['unresolved_components'] += 1
                    audit['whole_lemmas_unresolved'] += 1
                    audit['unresolved_lemmas_single'] += 1
                    continue
                ids = list(authorized)
                source = kind
                if kind == 'REVIEWED_OPAQUE_ROOT':
                    audit['reviewed_opaque_lemmas'] += 1
                elif kind == 'UNIQUE_SPELLING_FALLBACK':
                    audit['unique_spelling_fallback_components'] += 1
            if ids:
                if parts and len(self.candidates(parts[0])) > 1:
                    audit['ambiguous_resolved_by_kuz'] += 1
                if len(ids) > 1:
                    audit['legitimate_multiple_lemmas'] += 1
                    audit['legitimate_multiple_components'] += 1
                assign(lemma, tuple((item,) for item in ids), Assignment(source))

        for table, replace in self.anchor_passes():
            self._apply_anchors(table, replace, built, assign, audit)
        self._install_digits(built, assign)

        families = {
            lemma: self._families(lemma) for lemma in KUZNETSOVA.lemmas()
        }
        return BuiltBank(
            structures=_shared(built),
            families=_shared(families),
            seconds=time.perf_counter() - started,
            provenance=provenance if record else {},
            tikhonov_rows=(
                {lemma: TIKHONOV.all_rows_of(lemma) for lemma in TIKHONOV.lemmas()}
                if record else {}
            ),
            audit=dict(audit),
        )

    def _families(self, lemma):
        return KUZNETSOVA.families_of(lemma)

    def _assign_senses(self, lemma, senses, parts, kuz_ids, explicit_roots, assign, audit):
        """Sense lemmas: resolve the root side first, then append the sense."""
        explicit = explicit_roots.get(lemma)
        if explicit is not None:
            roots, source, refused = explicit, 'SEMANTIC_ROOT_ASSIGNMENT', ()
        elif len(parts) >= 2:
            roots, refused, source, _rows = self.explode(parts, set(kuz_ids))
        elif kuz_ids:
            roots, source, refused = tuple((item,) for item in kuz_ids), 'KUZNETSOVA', ()
        elif len(parts) == 1:
            ids, source, refused = self.authorize(parts[0], ())
            roots, refused = tuple((item,) for item in ids), tuple(refused)
        else:
            roots, source, refused = (), 'UNRESOLVED_SEMANTIC_NO_ROOT', ()
        rooted = []
        for name in senses:
            if name.startswith(('fam:', 'tikh:', 'root:')):
                rooted.append((name,))
                continue
            if not name.startswith('sense:'):
                raise RuntimeError(f'invalid semantic identity: {name}')
            rooted.extend(tuple(base) + (name,) for base in roots)
        if rooted:
            structures = tuple(sorted(set(rooted)))
            assign(lemma, structures, Assignment(
                'SEMANTIC_ROOT_ASSIGNMENT' if source == 'SEMANTIC_ROOT_ASSIGNMENT'
                else 'SEMANTIC_SPLIT_ROOTED',
                ','.join('|'.join(item) for item in structures),
                refused,
            ))
            if len(structures) > 1:
                audit['legitimate_multiple_lemmas'] += 1
            return
        # An unrooted sense stays unresolved until a root is assigned.
        assign(lemma, None, Assignment(
            source if source.startswith('UNRESOLVED') else 'UNRESOLVED_SEMANTIC_NO_ROOT',
            ','.join(senses),
            refused,
        ))
        audit['whole_lemmas_unresolved'] += 1

    def _apply_anchors(self, table, replace, built, assign, audit):
        counter = {
            'NEGATION_OF_BASE': 'negation', 'REVIEWED_NEST_ANCHOR': 'nest_anchor',
            'ADVERB_OF_ADJECTIVE': 'adverb_base', 'HALF_COMPOUND': 'half_compound',
        }[table.source]
        anchors = table.data
        if replace == 'always':
            for lemma, anchor in anchors.items():
                assign(lemma, tuple(sorted(built.get(anchor, ()) if anchor else ())),
                       Assignment(table.source, anchor))
                audit[f'{counter}_lemmas'] += 1
            return
        if replace == 'bare_subset':
            for lemma, anchor in anchors.items():
                base = built.get(anchor)
                if not base:
                    continue
                own = built.get(lemma)
                if own and (
                    any(part.startswith('sense:') for structure in own for part in structure)
                    or not _bare_roots(own) <= _bare_roots(base)
                ):
                    continue
                assign(lemma, base, Assignment(table.source, anchor))
                audit[f'{counter}_lemmas'] += 1
            return
        # rootless: chains (an anchor of an anchor) resolve up to depth 3.
        pending = {lemma: anchor for lemma, anchor in anchors.items() if not built.get(lemma)}
        for _depth in range(3):
            resolved = {lemma: anchor for lemma, anchor in pending.items() if built.get(anchor)}
            if not resolved:
                break
            for lemma, anchor in resolved.items():
                assign(lemma, built[anchor], Assignment(table.source, anchor))
                audit[f'{counter}_lemmas'] += 1
                del pending[lemma]
        audit[f'{counter}_unresolved'] += len(pending)

    def _install_digits(self, built, assign):
        """A digit spells its cardinal: «5» takes the structures of «пять».

        A cardinal the dictionaries left without a root gets its own
        ``root:numeral_N`` so the digit and the word still meet.
        """
        for row in reviewed.DIGIT_CARDINALS.data:
            digit, cardinal = row['digit'], fold(row['cardinal'])
            structures = built.get(cardinal, ())
            if not structures:
                structures = ((f'root:numeral_{digit}',),)
                assign(cardinal, structures, Assignment('NUMERAL_CARDINAL_ROOT', digit))
            assign(digit, structures, Assignment('NUMERAL_DIGIT_ROOT', cardinal))

    # ── component authorization ────────────────────────────────────────────

    def kuznetsova_ids(self, lemma: str) -> tuple[str, ...]:
        ids: list[str] = []
        for numbered in KUZNETSOVA.roots_of(lemma):
            root_id = game_root_id(numbered, KUZNETSOVA.family_of(numbered))
            if root_id not in ids:
                ids.append(root_id)
        return tuple(ids)

    def candidates(self, morph: str) -> tuple[str, ...]:
        """Every game root whose spelling matches this morph. Not an assignment."""
        return _candidates(morph, id(KUZNETSOVA.data))

    def authorize(self, morph, lemma_ids):
        """Authorized game roots of one Tikhonov component.

        The lemma's own Kuznetsova roots select among spelling candidates.
        Without them: a reviewed opaque root, else exactly one spelling match
        that is not rejected, else nothing.
        """
        candidates = self.candidates(morph)
        if lemma_ids:
            allowed = tuple(item for item in candidates if item in lemma_ids)
            if not allowed:
                return (), 'UNRESOLVED', candidates
            kind = 'KUZNETSOVA_MULTI' if len(allowed) > 1 else 'KUZNETSOVA'
            return allowed, kind, tuple(item for item in candidates if item not in allowed)
        opaque = self.opaque_root(morph)
        if opaque:
            return (opaque,), 'REVIEWED_OPAQUE_ROOT', ()
        spelling = plain(morph)
        if len(candidates) == 1:
            if (spelling, candidates[0]) in reviewed.REJECTED_FALLBACK.data:
                return (), 'UNRESOLVED', candidates
            return candidates, 'UNIQUE_SPELLING_FALLBACK', ()
        return (), 'UNRESOLVED', candidates

    def opaque_root(self, morph) -> str:
        """``tikh:<morph>`` for a reviewed Tikhonov-only spelling, else ``''``."""
        spelling = plain(morph)
        if spelling in reviewed.OPAQUE_ROOTS.data and not KUZNETSOVA.has_spelling(spelling):
            return f'tikh:{spelling}'
        return ''

    def explode(self, parts, lemma_ids):
        """Complete structures of a compound from authorized components only."""
        choices, kinds, rows, refused = [], [], [], []
        failed = False
        for part in parts:
            ids, kind, extra = self.authorize(part, lemma_ids)
            component = reviewed.COMPOUND_COMPONENTS.data.get(plain(part))
            if not ids and component:
                ids, kind = (component,), 'REVIEWED_COMPOUND_COMPONENT'
            rows.append({'kind': kind, 'n_cand': len(ids) + len(extra), 'n_auth': len(ids)})
            if not ids:
                failed = True
            else:
                choices.append(ids)
                kinds.append(kind)
            refused.extend(extra)
        refused = tuple(dict.fromkeys(refused))
        if failed:
            return (), refused, 'UNRESOLVED', rows
        product = 1
        for options in choices:
            product *= len(options)
            if product > _TOO_MANY_COMBINATIONS:
                return (), refused, 'UNRESOLVED_TOO_AMBIGUOUS', rows
        combos = [()]
        for options in choices:
            combos = [prev + (option,) for prev in combos for option in options]
        unique = []
        for combo in combos:
            ordered = tuple(sorted(combo))
            if ordered not in unique:
                unique.append(ordered)
        if all(kind == 'REVIEWED_OPAQUE_ROOT' for kind in kinds):
            source = 'REVIEWED_OPAQUE_ROOT'
        elif all(kind == 'UNIQUE_SPELLING_FALLBACK' for kind in kinds):
            source = 'UNIQUE_SPELLING_FALLBACK'
        elif len(unique) > 1:
            source = 'AUTHORIZED_ALTERNATIVES'
        else:
            source = 'KUZNETSOVA'
        return tuple(unique), refused, source, rows


@lru_cache(maxsize=65_536)
def _candidates(morph: str, _dictionary_id: int) -> tuple[str, ...]:
    # Keyed by the loaded dictionary so a reload never serves stale ids.
    ids: list[str] = []
    for numbered, family in KUZNETSOVA.morphs_spelled(morph):
        root_id = game_root_id(numbered, family)
        if root_id not in ids:
            ids.append(root_id)
    return tuple(ids)


def _bare_roots(structures) -> set[tuple[str, ...]]:
    return {
        tuple(part for part in structure if not part.startswith('sense:'))
        for structure in structures
    }


def _shared(mapping: dict) -> dict:
    """Intern keys and give equal values one object (~65k lemmas, ~6.5k structures)."""
    canonical: dict = {}
    return {
        sys.intern(key): canonical.setdefault(value, value)
        for key, value in ((key, _interned(value)) for key, value in mapping.items())
    }


def _interned(value):
    if isinstance(value, str):
        return sys.intern(value)
    return tuple(_interned(item) for item in value)


ROOT_BANK = RootBank()
