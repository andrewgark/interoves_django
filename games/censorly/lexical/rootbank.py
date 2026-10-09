"""Game root structures from lemma evidence, with a unique-spelling fallback.

A structure is a sorted tuple of root ids. Sorting drops order and keeps
repeats, so ``белоснежный`` matches ``снежно-белый`` and ``один-одинехонек``
does not match ``один``. A word never stores a partial component list:
if one required component has no authorized reading, the whole structure
is unresolved and cannot match by an empty root.

Every component is authorized the same way. An explicit one-root sense, or
Kuznetsova membership of this lemma, is authoritative. A spelling that maps
to exactly one game root is a fallback when the lemma has no conflicting
evidence. A spelling that maps to several game roots authorizes none of them.
A sense label on a multi-root lemma is not a root key unless that lemma is
in the curated atomic list.
"""

from __future__ import annotations

import os
from collections import Counter
from functools import lru_cache
from pathlib import Path

from games.censorly.lexical.core import fold
from games.censorly.lexical.russian.compiler import (
    _families,
    _lines,
    _norm,
    _plain,
    _strip_note,
)

_DICT_DIR = Path(__file__).resolve().parent / 'data' / 'dictionaries'
_KUZ_LEMMAS = Path(os.environ.get('CENSORLY_KUZ_LEMMAS', _DICT_DIR / 'lemmas_to_roots.tsv'))
_KUZ_GROUPS = Path(os.environ.get('CENSORLY_KUZ_GROUPS', _DICT_DIR / 'root_groups.txt'))
_TIKHONOV = Path(os.environ.get('CENSORLY_TIKHONOV', _DICT_DIR / 'RuMorphs-Lemmas.txt'))

# Historical families whose join would glue unrelated modern words.
# ``чит`` and ``чт`` stay one reading root. ``чет`` does not join them.
_READ_MORPHS = frozenset({'чит', 'чт', 'ч'})

# Reviewed assignments are family/lemma assignments, never pair edges. The
# opaque namespace is separate from Kuznetsova families by construction.
REVIEWED_OPAQUE_ROOTS = frozenset({'сенат'})
REVIEWED_DERIVED_ROOTS = {
    fold('иволговый'): (('fam:иволг',),),
}


def structures_of(lemma: str) -> tuple[tuple[str, ...], ...]:
    """Root structures of one dictionary lemma. Empty when unknown."""
    return _bank()[0].get(fold(lemma), ())


def families_of(lemma: str) -> tuple[str, ...]:
    """Kuznetsova family ids of a lemma, before the game-root split."""
    _bank()
    return _KUZ_FAMILY.get(fold(lemma), ())


def assignment_of(lemma: str) -> dict:
    """Where this lemma's game roots came from.

    ``refused`` lists game roots that share the Tikhonov spelling but were
    not assigned. An unresolved lemma has no structures, so it cannot match
    another unresolved lemma by an empty root.
    """
    _bank()
    folded = fold(lemma)
    info = _PROVENANCE.get(folded)
    if info is None:
        return {
            'structures': (),
            'source': 'ABSENT',
            'detail': '',
            'refused': (),
        }
    return {
        'structures': structures_of(folded),
        'source': info['source'],
        'detail': info['detail'],
        'refused': info['refused'],
    }


_KUZ_FAMILY: dict[str, tuple[str, ...]] = {}
_PROVENANCE: dict[str, dict] = {}
_SOURCE: dict[str, str] = {}
_COMPONENT_AUDIT: dict[str, int] = {}

# Curated only. Do not add a lemma because Tikhonov segmented it badly.
# Each entry is a separate decision that the whole word is one game family.
_ATOMIC_LEXICALIZED = frozenset({
    fold('красивенький'),
    fold('мелюзга'),
    fold('боевитость'),
    fold('горестный'),
    fold('духовенство'),
    fold('живьем'),
    fold('житейский'),
    fold('житийный'),
    fold('оживление'),
    fold('оживленность'),
    fold('оживлять'),
    fold('оживляться'),
    fold('отживлять'),
    fold('отживляться'),
    fold('питейный'),
    fold('подгорюниться'),
    fold('предельчество'),
    fold('пригорюниваться'),
    fold('пригорюниться'),
    fold('рабыня'),
    fold('росплывь'),
    fold('роспуск'),
    fold('роспуски'),
    fold('ткацкий'),
    fold('засучиться'),
    fold('затупиться'),
    fold('вспомнить'),
    # Proper-name compounds Tikhonov splits (петер+бург, ленин+град, …).
    fold('петербургский'),
    fold('петербуржец'),
    fold('петербурженка'),
    fold('ленинградский'),
    fold('ленинградец'),
    fold('ленинградка'),
    fold('француженка'),
})


def component_audit() -> dict[str, int]:
    """How Tikhonov components were authorized. Same rule for every component."""
    _bank()
    return dict(_COMPONENT_AUDIT)


def load_seconds() -> float:
    _bank()
    return _bank()[1]


@lru_cache(maxsize=1)
def _bank() -> tuple[dict[str, tuple[tuple[str, ...], ...]], float]:
    import time

    started = time.perf_counter()
    families = _families(_KUZ_GROUPS)
    # Keys keep superscripts: вод¹ and вод² are different families.
    morph_family: dict[str, str] = {}
    plain_hits: dict[str, list[tuple[str, str]]] = {}
    for morph, family in families.items():
        morph_family[morph] = family
        plain_hits.setdefault(_plain(morph), []).append((morph, family))

    kuz: dict[str, list[str]] = {}
    kuz_families: dict[str, list[str]] = {}
    for line in _lines(_KUZ_LEMMAS):
        raw_lemma, raw_root = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        numbered = _norm(raw_root.strip())
        if not lemma or not numbered:
            continue
        family = morph_family.get(numbered, numbered)
        kuz.setdefault(lemma, [])
        family_list = kuz_families.setdefault(lemma, [])
        if family not in family_list:
            family_list.append(family)
        root_id = _game_id(numbered, family)
        if root_id not in kuz[lemma]:
            kuz[lemma].append(root_id)
    global _KUZ_FAMILY
    _KUZ_FAMILY = {lemma: tuple(items) for lemma, items in kuz_families.items()}

    tikhonov: dict[str, tuple[str, ...]] = {}
    for line in _lines(_TIKHONOV):
        raw_lemma, seg = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        roots = []
        for part in seg.split('/'):
            if ':' not in part:
                continue
            morph, kind = part.rsplit(':', 1)
            if kind == 'ROOT':
                plain = _plain(morph)
                if plain:
                    roots.append(plain)
        if lemma and roots:
            tikhonov[lemma] = tuple(roots)

    audit: Counter[str] = Counter()
    built: dict[str, tuple[tuple[str, ...], ...]] = {}
    provenance: dict[str, dict] = {}
    # Sense-only lemmas (proper-name clusters) are absent from Kuznetsova and
    # Tikhonov; still install them so рим opens римский.
    lemmas = (
        set(kuz)
        | set(tikhonov)
        | set(_SENSE)
        | set(REVIEWED_DERIVED_ROOTS)
    )
    for lemma in lemmas:
        parts = tikhonov.get(lemma, ())
        senses = _SENSE.get(lemma) or ()
        if isinstance(senses, str):
            senses = (senses,)
        kuz_ids = kuz.get(lemma, ())

        shape = 'multi' if len(parts) >= 2 else 'single'
        cand_lists = [
            _candidate_ids(part, plain_hits, morph_family) for part in parts
        ]
        if parts:
            audit[f'tikhonov_lemmas_{shape}'] += 1
        for candidates in cand_lists:
            audit['components_total'] += 1
            if len(candidates) > 1:
                audit['ambiguous_spelling_components'] += 1
            elif len(candidates) == 1:
                audit['unique_candidate_components'] += 1
            else:
                audit['zero_candidate_components'] += 1
        # A sense on a multi-root lemma is metadata unless this lemma was
        # explicitly lexicalized as one family. It is not an extra {sense:X}.
        if senses and (len(parts) < 2 or lemma in _ATOMIC_LEXICALIZED):
            built[lemma] = tuple((item,) for item in senses)
            provenance[lemma] = {
                'source': (
                    'ATOMIC_LEXICALIZED' if len(parts) >= 2
                    else _SOURCE.get(lemma, 'SEMANTIC_SPLIT')
                ),
                'detail': ','.join(senses),
                'refused': (),
            }
            if len(senses) > 1:
                audit['legitimate_multiple_lemmas'] += 1
            for candidates in cand_lists:
                if len(candidates) > 1:
                    audit['ambiguous_resolved_by_sense'] += 1
            continue
        # Reviewed derived assignments are authoritative after explicit
        # semantic assignments, and before dictionary reconstruction. They
        # provide complete structures, never pair edges or partial compounds.
        reviewed = REVIEWED_DERIVED_ROOTS.get(lemma)
        if reviewed is not None:
            built[lemma] = reviewed
            provenance[lemma] = {
                'source': 'REVIEWED_DERIVED_ROOT',
                'detail': ','.join('|'.join(item) for item in reviewed),
                'refused': (),
            }
            audit['reviewed_derived_lemmas'] += 1
            continue
        if len(parts) >= 2:
            structs, refused, source, rows = _explode(
                parts, set(kuz_ids), plain_hits, morph_family,
            )
            for row in rows:
                if row['kind'] == 'UNIQUE_SPELLING_FALLBACK':
                    audit['unique_spelling_fallback_components'] += 1
                elif row['n_cand'] > 1 and row['n_auth']:
                    audit['ambiguous_resolved_by_kuz'] += 1
                if row['n_auth'] > 1:
                    audit['legitimate_multiple_components'] += 1
                if row['kind'] == 'UNRESOLVED':
                    audit['unresolved_components'] += 1
            if structs:
                built[lemma] = structs
                provenance[lemma] = {
                    'source': source,
                    'detail': '+'.join(parts),
                    'refused': refused,
                }
                if len(structs) > 1:
                    audit['legitimate_multiple_lemmas'] += 1
            else:
                provenance[lemma] = {
                    'source': source,
                    'detail': source + ' ' + '+'.join(parts),
                    'refused': refused,
                }
                audit['whole_lemmas_unresolved'] += 1
                audit[f'unresolved_lemmas_{shape}'] += 1
                if source == 'UNRESOLVED_TOO_AMBIGUOUS':
                    audit['too_ambiguous_lemmas'] += 1
            continue
        ids = list(kuz_ids)
        source = 'KUZNETSOVA_MULTI' if len(ids) > 1 else 'KUZNETSOVA'
        if not ids and len(parts) == 1:
            authorized, kind, extra = _authorize_component(
                parts[0], (), plain_hits, morph_family,
            )
            if authorized:
                ids = list(authorized)
                source = kind
                if kind == 'REVIEWED_OPAQUE_ROOT':
                    audit['reviewed_opaque_lemmas'] += 1
                elif kind == 'UNIQUE_SPELLING_FALLBACK':
                    audit['unique_spelling_fallback_components'] += 1
            else:
                provenance[lemma] = {
                    'source': 'UNRESOLVED',
                    'detail': f'morph {parts[0]} matches {len(extra)} game roots',
                    'refused': extra,
                }
                audit['unresolved_components'] += 1
                audit['whole_lemmas_unresolved'] += 1
                audit['unresolved_lemmas_single'] += 1
                continue
        if ids:
            if parts and len(cand_lists[0]) > 1:
                audit['ambiguous_resolved_by_kuz'] += 1
            if len(ids) > 1:
                audit['legitimate_multiple_lemmas'] += 1
                audit['legitimate_multiple_components'] += 1
            built[lemma] = tuple((item,) for item in ids)
            provenance[lemma] = {'source': source, 'detail': '', 'refused': ()}
    global _PROVENANCE, _COMPONENT_AUDIT
    _PROVENANCE = provenance
    _COMPONENT_AUDIT = dict(audit)
    return built, time.perf_counter() - started


_SUP = str.maketrans('', '', '¹²³⁴⁵⁶⁷⁸⁹⁰')


def _game_id(numbered: str, family: str) -> str:
    parts = family.split('|')
    plain_parts = {_plain(part) for part in parts}
    if 'чит' in plain_parts and 'чет' in plain_parts:
        plain = _plain(numbered)
        if plain in _READ_MORPHS or numbered in _READ_MORPHS:
            return 'root:read'
        return 'root:' + plain
    # голова and глава are one historical root and two modern words.
    if plain_parts == {'глав', 'голав', 'голов'}:
        return 'root:head' if _plain(numbered) != 'глав' else 'root:chief'
    return 'fam:' + family


def _candidate_ids(morph: str, plain_hits, morph_family) -> tuple[str, ...]:
    """Every game root whose spelling matches this morph. Not a lemma assignment."""
    plain = _plain(morph)
    hits = plain_hits.get(plain) or []
    ids: list[str] = []
    if hits:
        for numbered, family in hits:
            gid = _game_id(numbered, family)
            if gid not in ids:
                ids.append(gid)
        return tuple(ids)
    numbered = _norm(morph)
    family = morph_family.get(numbered)
    if family:
        return (_game_id(numbered, family),)
    return ()


def _ids_for_morph(morph: str, plain_hits, morph_family) -> tuple[str, ...]:
    """The game root of a morph spelling, or nothing.

    One spelling can sit in several numbered families. That is not evidence
    that this lemma belongs to all of them, and an empty structure must not
    become a root that matches other empty structures.
    """
    ids = _candidate_ids(morph, plain_hits, morph_family)
    if len(ids) == 1:
        return ids
    return ()


def _authorize_component(morph, lemma_ids, plain_hits, morph_family):
    """Authorized game roots of one component. Spelling is only a candidate.

    Kuznetsova membership of this lemma selects among candidates. With no
    lemma evidence, exactly one spelling match is a fallback. Several
    matches authorize nothing, and a unique spelling that disagrees with
    the lemma's own membership is a conflict, not a fallback.
    """
    candidates = _candidate_ids(morph, plain_hits, morph_family)
    if lemma_ids:
        allowed = tuple(item for item in candidates if item in lemma_ids)
        if not allowed:
            return (), 'UNRESOLVED', candidates
        kind = 'KUZNETSOVA_MULTI' if len(allowed) > 1 else 'KUZNETSOVA'
        refused = tuple(item for item in candidates if item not in allowed)
        return allowed, kind, refused
    opaque = _reviewed_opaque_id(morph, plain_hits)
    if opaque:
        return (opaque,), 'REVIEWED_OPAQUE_ROOT', ()
    if len(candidates) == 1:
        return candidates, 'UNIQUE_SPELLING_FALLBACK', ()
    return (), 'UNRESOLVED', candidates


def _reviewed_opaque_id(morph, plain_hits) -> str:
    """Return a reviewed opaque id only for a Tikhonov-only spelling."""
    spelling = _plain(morph)
    if spelling in REVIEWED_OPAQUE_ROOTS and not plain_hits.get(spelling):
        return f'tikh:{spelling}'
    return ''


def _explode(parts, lemma_ids, plain_hits, morph_family):
    """Complete structures from authorized component readings only.

    An unauthorized required part drops the whole structure, so
    ``{пчел, вод}`` never shrinks to ``{пчел}`` and a sense label is not
    added beside the compound. More than 32 authorized combinations are
    not truncated into a partial truth: root equality stays closed.
    """
    choices = []
    kinds = []
    rows = []
    refused: list[str] = []
    failed = False
    for part in parts:
        ids, kind, extra = _authorize_component(part, lemma_ids, plain_hits, morph_family)
        rows.append({
            'kind': kind,
            'n_cand': len(ids) + len(extra),
            'n_auth': len(ids),
        })
        if not ids:
            failed = True
        else:
            choices.append(ids)
            kinds.append(kind)
        refused.extend(extra)
    if failed:
        return (), tuple(dict.fromkeys(refused)), 'UNRESOLVED', rows
    product = 1
    for options in choices:
        product *= len(options)
        if product > 32:
            return (), tuple(dict.fromkeys(refused)), 'UNRESOLVED_TOO_AMBIGUOUS', rows
    combos = [()]
    for options in choices:
        combos = [prev + (option,) for prev in combos for option in options]
    unique = []
    for combo in combos:
        ordered = tuple(sorted(combo))
        if ordered not in unique:
            unique.append(ordered)
    if all(kind == 'REVIEWED_OPAQUE_ROOT' for kind in kinds):
        source = 'REVIEWED_OPAQUE_ROOT'
    elif all(kind == 'UNIQUE_SPELLING_FALLBACK' for kind in kinds):
        source = 'UNIQUE_SPELLING_FALLBACK'
    elif len(unique) > 1:
        source = 'AUTHORIZED_ALTERNATIVES'
    else:
        source = 'KUZNETSOVA'
    return tuple(unique), tuple(dict.fromkeys(refused)), source, rows


def _sense(lemmas: tuple[str, ...], name: str) -> None:
    for lemma in lemmas:
        folded = fold(lemma)
        _SENSE[folded] = (name,)
        _SOURCE[folded] = 'SEMANTIC_SPLIT'


def _set_readings(lemma: str, names: tuple[str, ...]) -> None:
    """Store alternative one-root readings. Not one compound of all of them."""
    folded = fold(lemma)
    _SENSE[folded] = tuple(names)
    _SOURCE[folded] = 'MANUAL_MULTI_READING'


_SENSE: dict[str, tuple[str, ...]] = {}

# One id per modern cluster. Lemmas not listed keep the dictionary family,
# so мать/матушка stay together while матка leaves.
_sense((
    'странный', 'странно', 'странность', 'странноватый',
), 'sense:strange')
_sense((
    'крупный', 'крупнеть', 'покрупнеть',
    'укрупнение', 'укрупненный', 'укрупнить', 'укрупниться', 'укрупнять', 'укрупняться',
    'разукрупнение', 'разукрупнить', 'разукрупниться', 'разукрупнять', 'разукрупняться',
), 'sense:size')
_sense(('чета', 'четья'), 'sense:couple')
_sense(('четный', 'нечетный', 'бессчетный'), 'sense:even')
_sense(('душный',), 'sense:stuffy')
_sense((
    'годный', 'годность', 'годиться', 'негодный', 'негодник', 'негодница',
    'негодяй', 'негодяйка', 'негодяйский', 'негодяйство', 'негодящий',
), 'sense:suit')
_sense((
    'погода', 'непогода', 'непогодь', 'погодка', 'погодно', 'погодный', 'погодок', 'невзгода',
), 'sense:weather')
_sense(('выгода', 'выгодный', 'безвыгодный'), 'sense:benefit')
_sense(('светский', 'светскость'), 'sense:society')
_sense(('судный',), 'sense:doomsday')
_sense(('горний',), 'sense:celestial')
_sense(('угореть', 'угорелый', 'угорать'), 'sense:fumes')
_sense((
    'матка', 'маточник', 'маточный', 'безматок', 'безматочный', 'матица', 'матичный',
), 'sense:uterus')
_sense(('наматывать', 'наматываться', 'наматывание'), 'sense:wind')
_sense((
    'материть', 'материться', 'матерный', 'матерщина', 'матерка', 'безматерный',
), 'sense:swear')
_sense((
    'почтение', 'почтеннейший', 'почтенность', 'почтенный', 'почти',
    'почтительность', 'почтительный', 'почтить', 'предпочтение',
    'предпочтительность', 'предпочтительный', 'причт', 'причтовый',
    'учтивец', 'учтивость', 'учтивый', 'чтить', 'чтица',
), 'sense:honor')
_sense((
    'белок', 'белковый', 'белковина', 'белочный', 'белочник',
), 'sense:protein')
_sense((
    'белка', 'белочка', 'бельчонок', 'беличий',
), 'sense:squirrel')
_sense(('уголовник', 'уголовный', 'уголовщина'), 'sense:criminal')
_sense((
    'обезглавить', 'обезглавиться', 'обезглавленный', 'обезглавливание',
    'обезглавливать', 'обезглавливаться', 'безглавый',
), 'root:head')

# Family-level modern splits. A lemma is listed in one cluster only.
# Lemmas that stay out of every cluster remain on the dictionary family
# and are unresolved, not silently treated as safe.
from games.censorly.lexical.semantic_splits import install as _install_splits
from games.censorly.lexical.proper_names import install as _install_proper_names

_install_splits(_sense, _set_readings)
_install_proper_names(_sense, _set_readings)
