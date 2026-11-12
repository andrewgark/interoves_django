"""Candidates for a salad's «Почти ответ» list: other forms of its answers.

An author lists near-answers by hand (КРАСКА when the answer is КРАСКИ).
This finds the forms an author may have missed: every form of an answer's
paradigm, by pymorphy (the analyzer Цензурки use too), that can be traced on
the grid and is not already an answer, a rare word or a near-answer.
"""

from __future__ import annotations

from functools import lru_cache

from games.puzzles.salad.rules import (
    find_paths,
    normalize_word,
    parse_grid,
    parse_near_words,
    parse_rare_words,
    parse_words,
)

# A form shorter than this is a stray syllable on the board, not a find.
NEAR_CANDIDATE_MIN_LENGTH = 3
NEAR_CANDIDATES_LIMIT = 30


@lru_cache(maxsize=2048)
def word_forms(word: str) -> tuple[str, ...]:
    """Normalized forms of every paradigm the word belongs to, the word included.

    Only dictionary parses count, and when the word reads as a noun only its
    noun paradigms do: a salad answer is a noun, and СТАЛИ should not bring
    in the forms of the verb стать.
    """
    surface = (word or '').strip().lower().replace('ё', 'е')
    if not surface or ' ' in surface:
        return ()
    from games.puzzles.core.norm_matcher import MORPH_ANALYZER

    parses = [
        parse for parse in MORPH_ANALYZER.parse(surface)
        if parse.methods_stack
        and type(parse.methods_stack[0][0]).__name__ == 'DictionaryAnalyzer'
    ]
    nouns = [parse for parse in parses if 'NOUN' in parse.tag]
    forms = []
    seen = set()
    for parse in nouns or parses:
        for form in parse.lexeme:
            normalized = normalize_word(form.word)
            if normalized and normalized not in seen:
                seen.add(normalized)
                forms.append(normalized)
    return tuple(forms)


def near_answer_candidates(grid, words, rare_words=(), near_words=()):
    """[{'word': form, 'answer': answer}] for forms traceable on the grid.

    Grouped by answer in the author's order, shortest forms first.
    """
    taken = {normalize_word(word) for word in [*words, *rare_words, *near_words]}
    result = []
    for answer in words:
        forms = [
            form for form in word_forms(answer)
            if form not in taken and len(form) >= NEAR_CANDIDATE_MIN_LENGTH
        ]
        for form in sorted(forms, key=lambda item: (len(item), item)):
            if not find_paths(grid, form, limit=1):
                continue
            taken.add(form)
            result.append({'word': form, 'answer': normalize_word(answer)})
            if len(result) >= NEAR_CANDIDATES_LIMIT:
                return result
    return result


def suggest_near_answers(grid_value, words_value, rare_words_value='', near_words_value=''):
    """Candidates for an editor's raw fields; [] while the fields do not parse."""
    try:
        grid = parse_grid(grid_value)
        words = parse_words(words_value)
        rare_words = parse_rare_words(rare_words_value)
        near_words = parse_near_words(near_words_value)
    except ValueError:
        return []
    return near_answer_candidates(grid, words, rare_words, near_words)
