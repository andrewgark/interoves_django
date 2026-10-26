"""Pick a backend for one token.

The article language is a prior, not a hard label. A single short token
is not run through language detection. Latin inside a Russian article
stays generic: ``Apis`` is not treated as English.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.match.core import fold, script_of
from games.puzzles.censorly.lexical.match.generic_backend import NAME as GENERIC

RUSSIAN = 'russian'
# Letters that mark Ukrainian, Belarusian or Serbian rather than Russian.
_NON_RUSSIAN_CYRILLIC = frozenset('іїєґўјљњћџђѓѕ')


def backend_name(token: str, *, article_language: str = '') -> str:
    if script_of(token) != 'Cyrl' or article_language not in ('', 'ru'):
        return GENERIC
    folded = fold(token)
    if any(char in _NON_RUSSIAN_CYRILLIC for char in folded):
        return GENERIC
    from games.puzzles.censorly.lexical.russian.morphology import readings
    if not any(item.pos for item in readings(token)):
        return GENERIC
    return RUSSIAN
