"""``Matcher``: does a guess open an article token, and by which layer.

Layers are asked in a fixed order; the first that links the pair names the
relation. Between them sit gates that close a pair regardless of later
layers:

```
exact spelling        (both open from the start, or the context refuses
                       its own spelling → nothing to reveal)
function-word gate    (an open function word opens only its own spelling;
                       pronouns and numerals go on)
context gate          (the token's context accepts only some guesses)
alias
digit gate            (a digit opens only its cardinal word)
service gate          (an initially open word: lexeme, pronoun root, label
                       root, numeral grammar — nothing else)
proper
Russian gate          (non-Russian tokens stop here)
lexeme → reverse lexeme → root → grammar
```
"""

from __future__ import annotations

from functools import cached_property

from games.puzzles.censorly.lexical import reviewed
from games.puzzles.censorly.lexical.context import context_of
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.deciders.layers import (
    ALIAS,
    EXACT,
    GRAMMAR,
    LEXEME,
    PROPER,
    REVERSE_LEXEME,
    ROOT,
)
from games.puzzles.censorly.lexical.deciders.visibility import VISIBILITY


class Matcher:
    @cached_property
    def _cardinal_of_digit(self) -> dict[str, str]:
        return {row['digit']: fold(row['cardinal']) for row in reviewed.DIGIT_CARDINALS.data}

    @cached_property
    def _service_grammar_numerals(self) -> frozenset[str]:
        """Cardinals 2–9 keep their ordinal grammar link while open (два/второй).

        «один»/«первый» are deliberately split.
        """
        return frozenset(self._cardinal_of_digit[str(digit)] for digit in range(2, 10))

    def numeric_root(self, guess, target) -> bool:
        """A digit and its cardinal word with a shared structure (5/пять, 200/двести)."""
        if not (guess.fold.isdigit() or target.fold.isdigit()):
            return False
        digit, other = (guess.fold, target) if guess.fold.isdigit() else (target.fold, guess)
        cardinal = self._cardinal_of_digit.get(digit)
        if not cardinal or not any(item.lemma == cardinal for item in other.reads):
            return False
        return bool(guess.roots & target.roots)

    def relation(self, guess, target) -> str:
        if not guess.fold or not target.fold:
            return ''
        context = context_of(target.contextual_reading)
        if EXACT.opens(guess, target):
            if guess.service and target.service:
                return ''
            return EXACT.name if context is None or context.exact_opens else ''
        if guess.service and not self.carries_meaning(guess):
            return ''
        if context is not None and context.guess_lemmas is not None:
            if LEXEME.opens(guess, target) and any(
                item.lemma in context.guess_lemmas for item in guess.reads
            ):
                return LEXEME.name
            return ''
        numeric = self.numeric_root(guess, target)
        if ALIAS.opens(guess, target):
            return ALIAS.name
        if (guess.fold.isdigit() or target.fold.isdigit()) and not numeric:
            return ''
        if (guess.service or target.service) and not numeric:
            return self._service_relation(guess, target)
        if PROPER.opens(guess, target):
            return PROPER.name
        if not guess.russian or not target.russian:
            return ''
        for layer in (LEXEME, REVERSE_LEXEME, ROOT, GRAMMAR):
            if layer.opens(guess, target):
                return layer.name
        return ''

    def _service_relation(self, guess, target) -> str:
        """An initially open word keeps only its own forms and narrow links."""
        if LEXEME.opens(guess, target):
            return LEXEME.name
        # Pronoun roots: этот/это.
        if ROOT.opens(guess, target) and any(
            VISIBILITY.is_pronoun_reading(item) for item in (*guess.reads, *target.reads)
        ):
            return ROOT.name
        if GRAMMAR.opens(guess, target) and any(
            item.lemma in self._service_grammar_numerals
            for item in (*guess.reads, *target.reads)
        ):
            return GRAMMAR.name
        return ''

    def near_miss(self, guess, target) -> str:
        """Why a closed pair is close: a shared root id or dictionary family."""
        if guess.service or target.service or not guess.russian or not target.russian:
            return ''
        if guess.root_ids & target.root_ids:
            return 'root_overlap'
        if guess.families & target.families:
            return 'family_split'
        return ''

    def carries_meaning(self, word) -> bool:
        """An open word that still links to others.

        Pronouns (это/эти) and 0–9 numerals in words or digits (два/второй,
        5/пять). Other open function words open only their own spelling.
        """
        if word.fold.isdigit() and word.roots:
            return True
        return any(
            VISIBILITY.is_pronoun_reading(item) or item.lemma in VISIBILITY.initial_numbers
            for item in word.reads
        )

    def guess_layers(self, guess):
        """Layers that can open something for this guess; the index looks up their keys."""
        if guess.service and not self.carries_meaning(guess):
            return (EXACT,)
        layers = [EXACT, ALIAS, PROPER]
        if guess.russian:
            layers += [LEXEME, REVERSE_LEXEME, ROOT, GRAMMAR]
        return tuple(layers)


MATCHER = Matcher()
