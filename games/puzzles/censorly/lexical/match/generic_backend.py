"""Exact identity for any Unicode token.

No stemming and no etymology. An unknown script or language opens only
the same normalized token.
"""

from __future__ import annotations

from games.censorly.lexical.core import EXACT_MATCH, LexicalNode, fold, script_of

CAPABILITIES = frozenset({EXACT_MATCH})
NAME = 'generic'


def node_for(token: str) -> LexicalNode:
    normalized = fold(token)
    return LexicalNode(
        backend=NAME,
        key=normalized,
        language='',
        script=script_of(token),
        normalized=normalized,
    )
