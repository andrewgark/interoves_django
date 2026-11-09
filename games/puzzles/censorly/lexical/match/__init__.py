"""Gameplay entry points of the Censorly matcher.

The logic lives in ``lexical.deciders``; this module only names the calls
gameplay, tools and tests use.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.deciders.analyzer import ANALYZER, Word
from games.puzzles.censorly.lexical.deciders.index import (
    ArticleIndex,
    analyze_guess,
    build_article_analysis,
    decision,
    matching_lemmas,
    same_word,
)
from games.puzzles.censorly.lexical.deciders.matcher import MATCHER

__all__ = [
    'ANALYZER',
    'MATCHER',
    'ArticleIndex',
    'Word',
    'analyze_guess',
    'build_article_analysis',
    'decision',
    'matching_lemmas',
    'same_word',
]
