"""Open article targets for an arbitrary guess.

Inflection and cognates are separate. Cognate expansion runs only when
every live reading of the guess is linked to every live reading of the
target. Unknown tokens match their normalized form and nothing else.
Gameplay still opens by lemma equality; this module is not wired in.
"""

from __future__ import annotations

from games.censorly.lexical.core import fold, script_of, stress_signature
from games.censorly.lexical.dispatcher import RUSSIAN, backend_name
from games.censorly.lexical.generic_backend import node_for
from games.censorly.lexical.relations.graph import neighbors, proof_between
from games.censorly.lexical.russian.context import (
    bare_target,
    covers_every_identity,
    guess_inflection_ids,
)
from games.censorly.lexical.russian.morphology import cognate_lemmas, lexeme_ids


def relation_kind(guess: str, target: str, *, article_language: str = 'ru') -> str:
    """``exact``, ``inflection``, ``cognate:<proof>`` or ``''``."""
    if not fold(guess) or not fold(target):
        return ''
    if _stress_conflict(guess, target):
        return ''
    if fold(guess) == fold(target):
        return 'exact'
    if backend_name(guess, article_language=article_language) != RUSSIAN:
        return ''
    if backend_name(target, article_language=article_language) != RUSSIAN:
        return ''
    if _same_lexeme(guess, bare_target(target)):
        return 'inflection'
    proof = _cognate_proof(guess, target)
    if proof:
        return 'cognate:' + proof
    return ''


def related(guess: str, target: str, *, article_language: str = 'ru') -> bool:
    """True when the guess may open this target. Unknown stays closed."""
    return bool(relation_kind(guess, target, article_language=article_language))


def open_targets(
    guess: str,
    targets: list[str],
    *,
    article_language: str = 'ru',
) -> list[str]:
    """Targets from a prepared article list that this guess opens.

    Inflection is checked against each target identity. Cognate neighbours
    come from one graph lookup, then an intersection — not a parse of every
    target against every root.
    """
    if not fold(guess):
        return []
    opened = []
    if backend_name(guess, article_language=article_language) != RUSSIAN:
        wanted = fold(guess)
        for target in targets:
            if target and fold(target) == wanted and not _stress_conflict(guess, target):
                opened.append(target)
        return opened
    allowed = _cognate_closure(guess)
    for target in targets:
        if not target or _stress_conflict(guess, target):
            continue
        if fold(guess) == fold(target):
            opened.append(target)
            continue
        if script_of(target) != 'Cyrl':
            continue
        if _same_lexeme(guess, bare_target(target)):
            opened.append(target)
            continue
        target_lemmas = _lemmas(target)
        if allowed and target_lemmas and target_lemmas <= allowed:
            opened.append(target)
    return opened


def describe_token(token: str, *, article_language: str = 'ru') -> dict[str, str]:
    """Diagnostic view of one arbitrary token. Does not open anything."""
    name = backend_name(token, article_language=article_language)
    generic = node_for(token)
    info = {
        'backend': name,
        'script': generic.script,
        'normalized': generic.normalized,
        'language': 'ru' if name == RUSSIAN else '',
    }
    if name == RUSSIAN:
        info['lemmas'] = ','.join(sorted(_lemmas(token)))
        info['lexemes'] = ','.join(
            f'{para}:{lemma}' for para, lemma in sorted(lexeme_ids(token))
        )
    return info


def prepare_target(surface: str, *, article_language: str = 'ru') -> dict[str, object]:
    """Metadata computed once per article token, then reused for every guess."""
    name = backend_name(surface, article_language=article_language)
    occurrence = bare_target(surface) if name == RUSSIAN else None
    return {
        'surface': surface,
        'normalized': fold(surface),
        'script': script_of(surface),
        'backend': name,
        'lexemes': lexeme_ids(surface) if name == RUSSIAN else frozenset(),
        'lemmas': _lemmas(surface) if name == RUSSIAN else frozenset(),
        'inflection_lexemes': occurrence.lexemes if name == RUSSIAN else frozenset(),
        'inflection_conservative': occurrence.conservative if name == RUSSIAN else False,
    }


def open_prepared(
    guess: str,
    prepared: list[dict[str, object]],
    *,
    article_language: str = 'ru',
) -> list[str]:
    """Open pre-indexed targets. Does not parse the article again."""
    if not fold(guess):
        return []
    if backend_name(guess, article_language=article_language) != RUSSIAN:
        wanted = fold(guess)
        return [
            str(item['surface'])
            for item in prepared
            if item.get('normalized') == wanted
            and not _stress_conflict(guess, str(item.get('surface') or ''))
        ]
    allowed = _cognate_closure(guess)
    guess_ids = guess_inflection_ids(guess)
    opened = []
    for item in prepared:
        surface = str(item.get('surface') or '')
        if not surface or _stress_conflict(guess, surface):
            continue
        if fold(guess) == item.get('normalized'):
            opened.append(surface)
            continue
        if item.get('script') != 'Cyrl':
            continue
        if _prepared_same_lexeme(guess_ids, item, surface):
            opened.append(surface)
            continue
        lemmas = item.get('lemmas') or frozenset()
        if allowed and lemmas and lemmas <= allowed:
            opened.append(surface)
    return opened


def _same_lexeme(guess: str, occurrence) -> bool:
    """Same lexeme, with a strict rule when the target identity is not one lemma."""
    guess_ids = guess_inflection_ids(guess)
    if not guess_ids or not occurrence.lexemes:
        return False
    if occurrence.conservative:
        return covers_every_identity(guess_ids, occurrence.lexemes)
    return bool(guess_ids & occurrence.lexemes)


def _prepared_same_lexeme(guess_ids, item, surface: str) -> bool:
    if 'inflection_lexemes' in item:
        lexemes = item.get('inflection_lexemes') or frozenset()
        conservative = bool(item.get('inflection_conservative'))
    else:
        occurrence = bare_target(surface)
        lexemes = occurrence.lexemes
        conservative = occurrence.conservative
    if not guess_ids or not lexemes:
        return False
    if conservative:
        return covers_every_identity(guess_ids, lexemes)
    return bool(guess_ids & lexemes)


def _cognate_proof(guess: str, target: str) -> str:
    """A proof only when every live lemma of both sides is linked.

    ``дневной`` is ``дневный`` or ``дневной``, and both are linked to ``день``.
    ``прибыли`` is ``прибыль`` or ``прибыть``, and neither is linked to ``быть``.
    One agreeing reading is not enough.
    """
    left = _lemmas(guess)
    right = _lemmas(target)
    if not left or not right:
        return ''
    found = ''
    for a in left:
        for b in right:
            if a == b:
                continue
            proof = proof_between(a, b)
            if not proof:
                return ''
            found = proof
    return found


def _cognate_closure(surface: str) -> frozenset[str]:
    """Lemmas reachable from every reading, including the readings themselves."""
    lemmas = _lemmas(surface)
    if not lemmas:
        return frozenset()
    sets = []
    for lemma in lemmas:
        sets.append(set(neighbors(lemma)) | {lemma})
    return frozenset(set.intersection(*sets))


def _lemmas(surface: str) -> frozenset[str]:
    if script_of(surface) != 'Cyrl':
        return frozenset()
    if backend_name(surface, article_language='ru') != RUSSIAN:
        return frozenset()
    return cognate_lemmas(surface)


def _stress_conflict(guess: str, target: str) -> bool:
    left = stress_signature(guess)
    right = stress_signature(target)
    return bool(left and right and left != right and fold(guess) == fold(target))
