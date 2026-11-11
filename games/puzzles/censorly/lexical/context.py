"""Article context: what the sentence decided about one token.

The tokenizer cannot store a parse tree, so it stores a marker in the token's
``lemma`` (``__censorly_tom_volume__``). Each marker is a ``TokenContext``:
the single description of what that occurrence means to the matcher.

* ``lemmas``: citation lemmas the token reads as («г.» after a year → год).
  A context whose lemma is its own marker («ПО», «Ли») has no expansion: only
  its exact spelling opens it.
* ``readings``: without ``lemmas``, the dictionary readings to keep
  («нем» outside a phrase → нем, немой).
* ``closed``: the token is hidden at the start even if its spelling is a
  function word («Д.» as an initial, «том» as a volume).
* ``exact_opens``: typing the spelling itself opens the token. «в.» (век)
  opens from век, not from the preposition «в».
* ``expands_guess``: in an article that has such a token, a guess spelled
  the same also reads as ``lemmas`` («порт» in an article with «порт.»
  also means португальский). Elsewhere «порт», «кит», «нем» stay ordinary.
* ``guess_lemmas``: only a guess that reads as one of these lemmas opens
  the token («о нём» opens from он/оно, not from немой). An empty set means
  only the exact spelling opens it (initials: «Д.» opens from «д» alone).

The tokenizer decides *where* a context applies; this module says *what*
it means. Nothing else in the matcher knows about markers.
"""

from __future__ import annotations

from dataclasses import dataclass

from games.puzzles.censorly.lexical.core import fold

_PREFIX = '__censorly_'
INITIAL_CONTEXT_PREFIX = '__censorly_initial_'


@dataclass(frozen=True)
class TokenContext:
    marker: str
    surface: str
    lemmas: tuple[str, ...] = ()
    readings: frozenset[str] | None = None
    closed: bool = True
    exact_opens: bool = True
    expands_guess: bool = False
    guess_lemmas: frozenset[str] | None = None

    @property
    def exact_only(self) -> bool:
        return self.lemmas == (self.marker,)


def _ctx(name, surface, **kwargs) -> TokenContext:
    marker = f'{_PREFIX}{name}__'
    lemmas = kwargs.pop('lemmas', ())
    if lemmas == 'self':
        lemmas = (marker,)
    return TokenContext(marker, surface, lemmas=lemmas, **kwargs)


NEM_PRONOUN = _ctx(
    'nem_pronoun', 'нем', lemmas=('он', 'оно'), closed=False,
    guess_lemmas=frozenset({'он', 'оно'}),
)
NEM_NONPRONOUN = _ctx('nem_nonpronoun', 'нем', readings=frozenset({'нем', 'немой'}))
NEM_GERMAN = _ctx('nem_german', 'нем', lemmas=('немец',), expands_guess=True)
TOM_VOLUME = _ctx('tom_volume', 'том', readings=frozenset({'том'}))
N_ERA = _ctx('n_era', 'н', lemmas=('наш',))
PO_SOFTWARE = _ctx('po_software', 'по', lemmas='self')
LI_NAME = _ctx('li_name', 'ли', lemmas='self')
YEAR_ABBR = _ctx('year_abbr', 'г', lemmas=('год',))
CITY_ABBR = _ctx('city_abbr', 'г', lemmas=('город',))
PAGE_ABBR = _ctx('page_abbr', 'стр', lemmas=('страница',))
STREET_ABBR = _ctx('street_abbr', 'ул', lemmas=('улица',))
CENTURY_ABBR = _ctx('century_abbr', 'в', lemmas=('век',), exact_opens=False)
# «5 см» is a unit; «(см. ниже)» is «смотри» and opens only from смотреть,
# not from the сантиметр alias.
CM_UNIT = _ctx('cm_unit', 'см', lemmas=('сантиметр',))
SEE_REF = _ctx('see_ref', 'см', lemmas=('смотреть',), guess_lemmas=frozenset({'смотреть'}))
# «теле-шоу»: not the noun тело. Visible like any content word.
TELE_PREFIX = _ctx('tele_prefix', 'теле', lemmas='self', closed=False)


def _language_labels() -> dict[str, TokenContext]:
    from games.puzzles.censorly.lexical.reviewed import LABELS

    return {
        row['label']: _ctx(
            f'{row["label"]}_language', row['label'], lemmas=(row['lemma'],), expands_guess=True,
        )
        for row in LABELS.data
        if row['kind'] == 'language_label'
    }


# Homographic language labels before a gloss: «(кит. 北京)», «от лат. felis».
LANGUAGE_LABELS = _language_labels()

_REGISTRY: dict[str, TokenContext] = {
    item.marker: item
    for item in (
        NEM_PRONOUN, NEM_NONPRONOUN, NEM_GERMAN, TOM_VOLUME, N_ERA,
        PO_SOFTWARE, LI_NAME, YEAR_ABBR, CITY_ABBR, PAGE_ABBR, STREET_ABBR,
        CENTURY_ABBR, TELE_PREFIX, CM_UNIT, SEE_REF, *LANGUAGE_LABELS.values(),
    )
}


def initial_context(letter: str) -> str:
    """Marker of an initial («Д. В. Григорович»): closed, opened by its letter."""
    return f'{INITIAL_CONTEXT_PREFIX}{letter}__'


def context_of(lemma: str) -> TokenContext | None:
    """The context a token lemma names, or ``None`` for an ordinary lemma."""
    if not lemma or not lemma.startswith(_PREFIX):
        return None
    found = _REGISTRY.get(lemma)
    if found is not None:
        return found
    if lemma.startswith(INITIAL_CONTEXT_PREFIX):
        letter = lemma[len(INITIAL_CONTEXT_PREFIX):-2]
        return TokenContext(lemma, fold(letter), guess_lemmas=frozenset())
    return None
