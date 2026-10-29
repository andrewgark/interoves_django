"""Лесенка: pure rules of one submitted word.

These are the rules the browser will mirror for offline play
(docs/offline-play-plan.md): which word may be checked at all, how a correct
word advances the ladder and which progress status the state gives. Points
are server-only and stay in ``score_word``; the client never mirrors them.
"""

from __future__ import annotations

from decimal import Decimal

from games.puzzles.raddle.engine import (
    playable_word_indices,
    resolve_assist_tiers,
    word_length_matches,
    word_solve_credit,
)


def refuse_word(state, parsed, word_index, word):
    """Why the word is not checked at all, or None when it may be checked."""
    n = parsed['n_words']
    if not word:
        return 'Пустой ответ'
    if word_index < 0 or word_index >= n:
        return 'Неверный индекс слова'
    if word_index in set(state.get('solved_indices') or []):
        return 'Это слово уже решено'
    if word_index not in playable_word_indices(state, n):
        return 'Сейчас можно сдавать только крайние нерешённые слова'
    if not word_length_matches(word, parsed['masks'][word_index]):
        return 'Длина слова не совпадает с маской'
    return None


def advance(state, parsed, word_index):
    """State after a correct word.

    The word becomes solved, and every clue linking it to an already solved
    neighbour counts as used.
    """
    solved_before = set(state.get('solved_indices') or [])
    used_hints = set(state.get('used_hints') or [])
    n_hints = len(parsed['hints'])
    if (word_index - 1) in solved_before and 0 <= word_index - 1 < n_hints:
        used_hints.add(word_index - 1)
    if (word_index + 1) in solved_before and 0 <= word_index < n_hints:
        used_hints.add(word_index)
    return {
        **state,
        'solved_indices': sorted(solved_before | {word_index}),
        'used_hints': sorted(used_hints),
    }


def progress_status(state, n_words, *, word_correct):
    """``(status, tournament_status)`` of the ladder after a checked word.

    The ladder is Ok when every middle word is solved and Partial once any is;
    in a tournament a wrong word stays Pending.
    """
    solved = set(state.get('solved_indices') or [])
    middle_total = max(0, n_words - 2)
    solved_middle = len(solved) - (2 if n_words >= 2 else len(solved))
    if middle_total and solved_middle >= middle_total:
        return 'Ok', 'Ok'
    status = 'Partial' if solved_middle > 0 else 'Wrong'
    return status, status if word_correct else 'Pending'


def score_word(state, parsed, word_index):
    """Points for solving ``word_index``: middle words only, reduced by assist."""
    if word_index in (0, parsed['n_words'] - 1):
        return Decimal('0')
    tier = resolve_assist_tiers(state).get(word_index, 0)
    return word_solve_credit(tier, parsed.get('assist') or {})
