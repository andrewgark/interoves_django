"""Spelling primitives shared by every part of the matcher."""

from __future__ import annotations

import re
import unicodedata

# Size of the per-surface caches (readings, analyses). An entry costs
# ~0.6 KB; a daily article has ~6k distinct surfaces, so this keeps several
# articles and their guesses warm at ~20 MB per web process.
WORD_CACHE_SIZE = 32_768


# Latin letters that look like Cyrillic ones, after casefold. A word that
# mixes them with Cyrillic was typed on two layouts (Счëт with Latin ë,
# Cтол with Latin C); uppercase M, T, H, B fold to m, t, h, b.
_HOMOGLYPHS = str.maketrans({
    'a': 'а', 'b': 'в', 'c': 'с', 'e': 'е', 'ë': 'ё', 'h': 'н', 'k': 'к', 'm': 'м',
    'o': 'о', 'p': 'р', 't': 'т', 'x': 'х', 'y': 'у',
})
_CYRILLIC = re.compile('[а-яё]')
_LATIN = re.compile('[a-zë]')
_LETTER_RUN = re.compile(r'[^\W\d_]+')


def _cyrillic_run(match: re.Match) -> str:
    run = match.group()
    return run.translate(_HOMOGLYPHS) if _CYRILLIC.search(run) else run


def fold(text: str) -> str:
    """NFC, strip combining marks, casefold, ё→е.

    Stress marks are removed. Callers that need stress must read the raw
    string before folding. Latin look-alikes inside a Cyrillic word become
    Cyrillic (Счëт → счет); a Latin part of a hyphenated word stays.
    """
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(ch for ch in folded if unicodedata.category(ch) != 'Mn')
    folded = folded.casefold()
    if _LATIN.search(folded) and _CYRILLIC.search(folded):
        # Only inside a run of letters that already has Cyrillic: pH-метр
        # and IT-компания keep their Latin half.
        folded = _LETTER_RUN.sub(_cyrillic_run, folded)
    return folded.replace('ё', 'е').strip()


def script_of(text: str) -> str:
    """Coarse Unicode script of the letters. Mixed tokens stay ``Zyyy``."""
    folded = fold(text)
    letters = [ch for ch in folded if ch.isalpha()]
    if not letters:
        return 'Zyyy'
    kinds = set()
    for ch in letters:
        if 'а' <= ch <= 'я' or ch == 'ё':
            kinds.add('Cyrl')
        elif 'a' <= ch <= 'z':
            kinds.add('Latn')
        elif 'Ͱ' <= ch <= 'Ͽ' or 'ἀ' <= ch <= '῿':
            kinds.add('Grek')
        else:
            kinds.add('Zyyy')
    if len(kinds) == 1:
        return next(iter(kinds))
    return 'Zyyy'
