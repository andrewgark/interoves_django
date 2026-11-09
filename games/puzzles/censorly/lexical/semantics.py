"""Pair-level calls for tools and tests.

``explain(guess, target)`` is exactly what gameplay decides for an isolated
guess and an isolated article token: the same analyzer and matcher, no
article context. Logic lives in ``lexical.deciders``.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.deciders.analyzer import ANALYZER
from games.puzzles.censorly.lexical.deciders.matcher import MATCHER
from games.puzzles.censorly.lexical.deciders.readings import READINGS
from games.puzzles.censorly.lexical.deciders.visibility import VISIBILITY

readings_of = READINGS.all
guess_readings_of = READINGS.for_guess
preferred_readings_of = READINGS.for_target
label_lemmas = READINGS.label_lemmas
initially_open = VISIBILITY.initially_open


def explain(guess: str, target: str, *, target_lemma: str = '') -> str:
    """The relation layer that opens ``target`` for ``guess``, or ``''``."""
    return MATCHER.relation(
        ANALYZER.guess(guess),
        ANALYZER.target(target, article_lemma=target_lemma),
    )


def opens(guess: str, target: str, *, target_lemma: str = '') -> bool:
    return bool(explain(guess, target, target_lemma=target_lemma))
