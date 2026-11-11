"""``ReadingSelector``: which dictionary readings the game believes.

Morphology lists every parse of a spelling. A guess has no sentence, so the
selector prefers the reading whose citation form *is* the spelling; an
article token may additionally carry the lemma its sentence chose. Every
reading correction we maintain (``rejected_readings.tsv``,
``target_readings.tsv``, ``fixed_readings.tsv``, ``labels.tsv``) is applied
here and nowhere else.
"""

from __future__ import annotations

from functools import cached_property, lru_cache

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.context import context_of
from games.puzzles.censorly.lexical.core import WORD_CACHE_SIZE, fold
from games.puzzles.censorly.lexical.sources import MORPHOLOGY, Reading

# A lone «е»/«ш» in article text is the letter, never a form of быть or a verb.
_LETTER_NAMES = frozenset({'е', 'ш'})
# Regular first halves of hyphenated compounds: русско-, политико-, немецко-.
_COMBINING_SUFFIXES = (('ико', 'ический'), ('ско', 'ский'), ('цко', 'цкий'))


class ReadingSelector:
    # ── tables ─────────────────────────────────────────────────────────────

    @cached_property
    def _rejected(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            (fold(row['surface']), fold(row['lemma']), row['pos'])
            for row in reviewed.REJECTED_READINGS.data
        )

    @cached_property
    def _target(self) -> dict[tuple[str, str], tuple[tuple[str, ...], str]]:
        found = {}
        for row in reviewed.TARGET_READINGS.data:
            if row['mode'] not in ('filter', 'substitute', 'closed'):
                reviewed.TARGET_READINGS.fail(f'unknown mode {row["mode"]!r}')
            found[(fold(row['surface']), fold(row['article_lemma']))] = (
                tuple(fold(item) for item in row['allowed'].split()), row['mode'],
            )
        return found

    @cached_property
    def _fixed(self) -> dict[str, tuple[str, bool]]:
        return {
            fold(row['surface']): (fold(row['lemma']), row['capitalized'] == 'yes')
            for row in reviewed.FIXED_READINGS.data
        }

    @cached_property
    def _labels(self) -> dict[str, str]:
        return {
            fold(row['label']): fold(row['lemma'])
            for row in reviewed.LABELS.data
            if row['kind'] in ('stub', 'combining')
        }

    # ── context-free readings ──────────────────────────────────────────────

    @lru_cache(maxsize=WORD_CACHE_SIZE)
    def all(self, surface: str) -> tuple[Reading, ...]:
        """Every dictionary reading except reviewed impossible ones."""
        folded = fold(surface)
        return tuple(
            item for item in MORPHOLOGY.readings(surface)
            if (folded, item.lemma, item.pos) not in self._rejected
        )

    def _letters(self, surface: str) -> tuple[Reading, ...]:
        found = self.all(surface)
        folded = fold(surface)
        if folded in _LETTER_NAMES:
            return tuple(item for item in found if item.lemma == folded)
        return found

    @lru_cache(maxsize=WORD_CACHE_SIZE)
    def for_guess(self, surface: str) -> tuple[Reading, ...]:
        """Readings of a typed word, and the readings used for root matching.

        When the spelling is the citation form of some reading, keep only
        those (an «-о» adverb also keeps its short adjective, so близко
        reaches близкий). An adverb or particle that is also a comparative
        keeps the comparative (больше reaches большой, лучше reaches хороший).
        Otherwise keep every reading (прибыли).
        """
        found = self._letters(surface)
        folded = fold(surface)
        if not folded:
            return found
        citation = tuple(item for item in found if item.lemma == folded)
        if any(item.pos in ('ADVB', 'PRCL') for item in citation):
            kept = ('ADJS', 'COMP') if any(item.pos == 'ADVB' for item in citation) else ('COMP',)
            citation += tuple(
                item for item in found
                if item.pos in kept and item not in citation
            )
        return citation or found

    @lru_cache(maxsize=WORD_CACHE_SIZE)
    def for_target(self, surface: str) -> tuple[Reading, ...]:
        """Readings of an isolated article token, used for same-lexeme matching.

        A noun citation form beats a homographic participle or short
        adjective, but other noun readings stay (улья: rare noun and genitive
        of улей). A non-noun citation form only drops a competing noun.
        """
        found = self._letters(surface)
        folded = fold(surface)
        if not folded:
            return found
        citation = tuple(item for item in found if item.lemma == folded)
        if not citation:
            return found
        if any(item.pos == 'NOUN' for item in citation):
            return tuple(item for item in found if item.lemma == folded or item.pos == 'NOUN')
        if any(item.pos == 'NOUN' for item in found if item.lemma != folded):
            return citation
        return found

    # ── what the article or a fixed spelling decides ───────────────────────

    def article_lemmas(self, surface: str, article_lemma: str) -> tuple[str, ...]:
        """Citation lemmas an article context substitutes for the token."""
        context = context_of(article_lemma)
        if context is None:
            found = self._target.get((fold(surface), fold(article_lemma)))
            return found[0] if found and found[1] == 'substitute' else ()
        if fold(surface) != context.surface:
            return ()
        return context.lemmas

    def article_filter(self, surface: str, article_lemma: str) -> frozenset[str] | None:
        """Lemmas an article occurrence keeps, or ``None`` when unrestricted."""
        context = context_of(article_lemma)
        if context is not None:
            return context.readings if fold(surface) == context.surface else None
        found = self._target.get((fold(surface), fold(article_lemma)))
        return frozenset(found[0]) if found and found[1] != 'substitute' else None

    def closed_in_article(self, surface: str, article_lemma: str) -> bool:
        """Whether the sentence hides a token whose spelling is a function word."""
        context = context_of(article_lemma)
        if context is not None:
            return context.closed
        found = self._target.get((fold(surface), fold(article_lemma)))
        return bool(found and found[1] == 'closed')

    def fixed_lemma(self, surface: str, *, in_article: bool = False) -> str:
        """The lemma a spelling always reads as (лет → год, Трои → троя).

        In the article only capitalized rows apply: the tokenizer already
        chose the lemma of an ordinary word.
        """
        found = self._fixed.get(fold(surface))
        if not found:
            return ''
        lemma, capitalized = found
        if capitalized and not (surface and surface[0].isupper()):
            return ''
        if in_article and not capitalized:
            return ''
        return lemma

    @lru_cache(maxsize=WORD_CACHE_SIZE)
    def label_lemmas(self, surface: str) -> tuple[str, ...]:
        """Full lemma of a label spelling: фр → французский, юго → юг, русско → русский."""
        from games.puzzles.censorly.lexical.deciders.rootbank import ROOT_BANK

        # Labels are written with е; «нём» is the pronoun, not «нем.».
        if 'ё' in (surface or '').lower():
            return ()
        word = fold(surface)
        lemma = self._labels.get(word)
        if lemma:
            return (lemma,)
        for half, full in _COMBINING_SUFFIXES:
            if len(word) > len(half) + 2 and word.endswith(half):
                lemma = word[:-len(half)] + full
                if ROOT_BANK.structures_of(lemma):
                    return (lemma,)
        return ()

    def predicted_lemma_in(self, surface: str, lemmas) -> str:
        """First analyzer lemma, predicted parses included, that is in ``lemmas``."""
        for lemma in MORPHOLOGY.all_lemmas(surface):
            if lemma in lemmas:
                return lemma
        return ''


READINGS = ReadingSelector()
