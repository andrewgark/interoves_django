"""Russian morphology and the approved derivation graph."""

from games.censorly.lexical.core import DERIVATION_GRAPH, EXACT_MATCH, INFLECTION

CAPABILITIES = frozenset({EXACT_MATCH, INFLECTION, DERIVATION_GRAPH})