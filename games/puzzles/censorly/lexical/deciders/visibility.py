"""``Visibility``: is a word open before the player guesses anything.

Function words, unambiguous pronouns and the numerals 0–9 start open. The
tokenizer's stop list (``text/stopwords.py``) removes prepositions,
conjunctions and particles from matching altogether; this decider handles
the rest from the word's readings, and ``visibility.tsv`` lists reviewed
exceptions. An article context may hide a token whose spelling is open
(«в.» as век, «том» as a volume).
"""

from __future__ import annotations

from functools import cached_property

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.deciders.readings import READINGS

_SERVICE_POS = frozenset({'PREP', 'CONJ', 'PRCL'})
_CONTENT_POS = frozenset({
    'NOUN', 'VERB', 'INFN', 'ADJF', 'ADJS', 'NUMR', 'NPRO', 'ADVB',
    'GRND', 'PRTF', 'PRTS', 'COMP', 'PRED',
})
INITIAL_ORDINALS = frozenset({
    'первый', 'второй', 'третий', 'четвертый', 'пятый',
    'шестой', 'седьмой', 'восьмой', 'девятый',
})
_DIGITS = frozenset(str(number) for number in range(10))


class Visibility:
    @cached_property
    def _rules(self) -> dict[str, frozenset[str]]:
        found: dict[str, set[str]] = {}
        for row in reviewed.VISIBILITY.data:
            found.setdefault(row['rule'], set()).add(fold(row['word']))
        return {rule: frozenset(words) for rule, words in found.items()}

    @property
    def pronoun_adjectives(self) -> frozenset[str]:
        """Pronoun lemmas pymorphy tags as ADJF/NUMR (этот, весь, один)."""
        return self._rules['pronoun_adjective']

    @cached_property
    def initial_numbers(self) -> frozenset[str]:
        """Cardinals 0–9 and ordinals 1–9: open from the start."""
        cardinals = {
            fold(row['cardinal']) for row in reviewed.DIGIT_CARDINALS.data
            if row['digit'] in _DIGITS
        }
        return frozenset(cardinals) | INITIAL_ORDINALS

    def is_pronoun_reading(self, reading) -> bool:
        return reading.pos == 'NPRO' or reading.lemma in self.pronoun_adjectives

    def initially_open(self, surface: str, *, include_pronouns: bool = True) -> bool:
        """Function words, unambiguous pronouns and 0–9 numerals start open.

        A competing content reading within 15% of the pronoun reading, or
        within 0.15 of the service reading, keeps the word closed.
        """
        folded = fold(surface)
        if folded in self._rules['never_open']:
            return False
        if folded in self._rules['always_open']:
            return True
        if folded in _DIGITS:
            return True
        found = READINGS.all(surface)
        if not found:
            return False
        if any(item.lemma in self.initial_numbers for item in found):
            return True
        best = max(item.score for item in found)
        pronoun_scores = [item.score for item in found if self.is_pronoun_reading(item)]
        if include_pronouns and pronoun_scores and max(pronoun_scores) >= best - 1e-9:
            pronoun_best = max(pronoun_scores)
            other_scores = [item.score for item in found if not self.is_pronoun_reading(item)]
            if not other_scores or max(other_scores) < pronoun_best * 0.85:
                return True
        if not any(item.pos in _SERVICE_POS and item.score >= best - 1e-9 for item in found):
            return False
        service_best = max(item.score for item in found if item.pos in _SERVICE_POS)
        content_scores = [item.score for item in found if item.pos in _CONTENT_POS]
        if not content_scores:
            return True
        if max(content_scores) >= service_best - 0.15:
            return False
        if sum(content_scores) >= service_best - 1e-9:
            return False
        return True

    def open_in_article(
        self, surface: str, article_lemma: str, *, include_pronouns: bool = True,
    ) -> bool:
        if article_lemma and READINGS.closed_in_article(surface, article_lemma):
            return False
        return self.initially_open(surface, include_pronouns=include_pronouns)


VISIBILITY = Visibility()
