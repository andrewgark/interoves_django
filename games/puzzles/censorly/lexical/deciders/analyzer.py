"""``WordAnalyzer``: one spelling (+ article context) → ``Word``.

A ``Word`` is everything the relation layers compare: readings, lexemes,
root structures, visibility. Guesses and article tokens go through the same
analysis; where they differ is listed here once:

=================  ===============================  ===============================
                   guess                            article token
=================  ===============================  ===============================
readings           ``for_guess`` (citation first)   ``for_target`` (noun first)
root readings      same as readings                 ``for_guess`` of the spelling
fixed lemma        лет → год, Троя                  capitalized rows only (Троя)
article context    only when the article has this   the token's own lemma
                   spelling with one lemma
visibility         ``initially_open``               also hidden by its context
reverse lexeme     ``lexemes_by_fold``              —
=================  ===============================  ===============================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold, script_of
from games.puzzles.censorly.lexical.deciders.readings import READINGS
from games.puzzles.censorly.lexical.deciders.rootbank import ROOT_BANK
from games.puzzles.censorly.lexical.deciders.visibility import VISIBILITY

# Hyphen and non-breaking hyphen join a compound. Dashes do not.
_HYPHEN_RE = re.compile(r'[-‐‑]')
_DASH = frozenset('–—―')
_MAX_HYPHEN_COMBINATIONS = 32


@dataclass(eq=False)
class Word:
    surface: str
    fold: str
    service: bool
    russian: bool
    reads: tuple
    lexemes: set
    roots: set
    families: set
    contextual_reading: str = ''
    # guess only: (paradigm, lemma) of every reading, by folded lemma.
    lexemes_by_fold: dict = field(default_factory=dict)
    is_guess: bool = False

    @property
    def root_ids(self) -> frozenset[str]:
        return frozenset(
            part for item in self.roots for part in item if not part.startswith('sense:')
        )


class WordAnalyzer:
    # ── structures of a spelling ───────────────────────────────────────────

    def structures(self, readings, surface: str, *, include_surface: bool = True) -> set:
        """Root structures of a spelling through its readings.

        With ``include_surface`` the spelling itself, its label lemma (фр →
        французский) and a reviewed lemma among predicted parses also count.
        A hyphenated spelling absent from the bank takes every combination of
        its parts' structures.
        """
        keys = set()
        if include_surface:
            keys.update(ROOT_BANK.structures_of(fold(surface)))
            for lemma in READINGS.label_lemmas(surface):
                keys.update(ROOT_BANK.structures_of(lemma))
        for item in readings:
            keys.update(ROOT_BANK.structures_of(item.lemma))
        if include_surface:
            reviewed_lemma = self.reviewed_derived_lemma(surface)
            if reviewed_lemma:
                keys.update(ROOT_BANK.structures_of(reviewed_lemma))
        keys.update(self.hyphen_structures(surface))
        return keys

    def reviewed_derived_lemma(self, surface: str) -> str:
        """A ``reviewed_derived_roots`` lemma reached through any parse, predicted too."""
        if not surface:
            return ''
        return READINGS.predicted_lemma_in(surface, reviewed.DERIVED_ROOTS.data)

    def hyphen_structures(self, surface: str) -> tuple[tuple[str, ...], ...]:
        raw = surface or ''
        if any(char in raw for char in _DASH) or not _HYPHEN_RE.search(raw):
            return ()
        if ROOT_BANK.structures_of(fold(raw)):
            return ()
        parts = [part for part in _HYPHEN_RE.split(raw) if part]
        if len(parts) < 2:
            return ()
        combos = [()]
        for part in parts:
            structures = []
            for item in READINGS.all(part):
                structures.extend(ROOT_BANK.structures_of(item.lemma))
            for lemma in READINGS.label_lemmas(part):
                structures.extend(ROOT_BANK.structures_of(lemma))
            if not structures:
                return ()
            combos = [prev + structure for prev in combos for structure in structures]
            if len(combos) > _MAX_HYPHEN_COMBINATIONS:
                return ()
        return tuple(tuple(sorted(combo)) for combo in combos)

    def is_russian(self, surface: str) -> bool:
        """Whether the Russian layers (lexeme, root, grammar) may compare it."""
        if (surface or '').strip().isdigit() and ROOT_BANK.structures_of(fold(surface)):
            return True
        if READINGS.label_lemmas(surface):
            return True
        if script_of(surface) != 'Cyrl':
            return False
        if any(item.pos for item in READINGS.all(surface)):
            return True
        return bool(ROOT_BANK.structures_of(fold(surface)) or self.reviewed_derived_lemma(surface))

    # ── words ──────────────────────────────────────────────────────────────

    def target(
        self, surface: str, *, article_lemma: str = '', include_pronouns: bool = True,
    ) -> Word:
        """An article token, read in its sentence when ``article_lemma`` says so."""
        folded = fold(surface)
        reads = READINGS.for_target(surface)
        root_reads = READINGS.for_guess(surface)
        contextual = False
        substituted: tuple[str, ...] = ()
        fixed = READINGS.fixed_lemma(surface, in_article=True)
        if fixed:
            reads = root_reads = ()
            contextual = True
        elif article_lemma:
            substituted = READINGS.article_lemmas(surface, article_lemma)
            if substituted:
                reads = tuple(
                    item for lemma in substituted
                    for item in READINGS.for_target(lemma)
                    if fold(item.lemma) == fold(lemma)
                )
                contextual = True
            else:
                allowed = READINGS.article_filter(surface, article_lemma)
                if allowed is not None:
                    reads = tuple(item for item in reads if fold(item.lemma) in allowed)
                    contextual = True
            if contextual:
                root_reads = reads
        if fixed:
            roots = set(ROOT_BANK.structures_of(fixed))
        elif substituted:
            roots = {s for lemma in substituted for s in ROOT_BANK.structures_of(lemma)}
        else:
            roots = self.structures(root_reads, surface, include_surface=not contextual)
        return Word(
            surface=surface,
            fold=folded,
            service=VISIBILITY.open_in_article(
                surface, article_lemma, include_pronouns=include_pronouns,
            ),
            russian=bool(fixed or substituted) or self.is_russian(surface),
            reads=reads,
            lexemes={(item.para_id, item.lemma) for item in reads},
            roots=roots,
            families=self._families(folded, reads),
            contextual_reading=article_lemma or '',
        )

    def guess(
        self, surface: str, *, article_lemma: str = '', include_pronouns: bool = True,
    ) -> Word:
        """A typed guess; ``article_lemma`` when the article settles its reading."""
        if not article_lemma:
            return _context_free_guess(surface, include_pronouns)
        return self._guess(surface, article_lemma, include_pronouns)

    def _guess(self, surface, article_lemma, include_pronouns) -> Word:
        folded = fold(surface)
        fixed = READINGS.fixed_lemma(surface)
        if fixed:
            reads = tuple(item for item in READINGS.for_guess(fixed) if fold(item.lemma) == fixed)
        else:
            reads = READINGS.for_guess(surface)
        contextual = False
        if article_lemma:
            substituted = READINGS.article_lemmas(surface, article_lemma)
            if substituted:
                reads = tuple(item for lemma in substituted for item in READINGS.for_guess(lemma))
                contextual = True
            else:
                allowed = READINGS.article_filter(surface, article_lemma)
                if allowed is not None:
                    reads = tuple(item for item in reads if fold(item.lemma) in allowed)
                    contextual = True
        if fixed:
            roots = set(ROOT_BANK.structures_of(fixed))
        else:
            roots = self.structures(reads, surface, include_surface=not contextual or not reads)
        lexemes_by_fold: dict[str, set] = {}
        # A fixed reading (Трои → троя) also decides which citation forms the
        # guess reaches; the spelling's other parses (троить) do not.
        for item in (reads if fixed else READINGS.for_target(surface)):
            lexemes_by_fold.setdefault(fold(item.lemma), set()).add((item.para_id, item.lemma))
        return Word(
            surface=surface,
            fold=folded,
            service=VISIBILITY.initially_open(surface, include_pronouns=include_pronouns),
            russian=bool(fixed) or self.is_russian(surface),
            reads=reads,
            lexemes={(item.para_id, item.lemma) for item in reads},
            roots=roots,
            families=self._families(folded, reads),
            lexemes_by_fold={key: frozenset(value) for key, value in lexemes_by_fold.items()},
            is_guess=True,
        )

    @staticmethod
    def _families(folded: str, reads) -> set:
        families = set(ROOT_BANK.families_of(folded))
        for item in reads:
            families.update(ROOT_BANK.families_of(item.lemma))
        return families


ANALYZER = WordAnalyzer()


@lru_cache(maxsize=512)
def _context_free_guess(surface: str, include_pronouns: bool) -> Word:
    """Article-independent guesses are reused across renders and articles."""
    return ANALYZER._guess(surface, '', include_pronouns)
