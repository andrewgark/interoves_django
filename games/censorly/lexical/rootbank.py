"""Game root structures from Kuznetsova families and Tikhonov compounds.

A structure is a sorted tuple of root ids. Sorting drops order and keeps
repeats, so ``белоснежный`` matches ``снежно-белый`` and ``один-одинехонек``
does not match ``один``. A compound never stores its single components as
extra structures: subset matching is not a reading.
"""

from __future__ import annotations

import os
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


def structures_of(lemma: str) -> tuple[tuple[str, ...], ...]:
    """Root structures of one dictionary lemma. Empty when unknown."""
    return _bank()[0].get(fold(lemma), ())


def families_of(lemma: str) -> tuple[str, ...]:
    """Kuznetsova family ids of a lemma, before the game-root split."""
    _bank()
    return _KUZ_FAMILY.get(fold(lemma), ())


_KUZ_FAMILY: dict[str, tuple[str, ...]] = {}


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

    built: dict[str, tuple[tuple[str, ...], ...]] = {}
    lemmas = set(kuz) | set(tikhonov)
    for lemma in lemmas:
        parts = tikhonov.get(lemma, ())
        if len(parts) >= 2:
            built[lemma] = _explode(parts, plain_hits, morph_family)
            continue
        ids = list(kuz.get(lemma, ()))
        if not ids and len(parts) == 1:
            ids = list(_ids_for_morph(parts[0], plain_hits, morph_family))
        senses = _SENSE.get(lemma) or ()
        if isinstance(senses, str):
            senses = (senses,)
        if senses:
            # One structure per reading. Several readings stay alternatives.
            built[lemma] = tuple((item,) for item in senses)
        elif ids:
            built[lemma] = tuple((item,) for item in ids)
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


def _ids_for_morph(morph: str, plain_hits, morph_family) -> tuple[str, ...]:
    plain = _plain(morph)
    hits = plain_hits.get(plain)
    if not hits:
        numbered = _norm(morph)
        family = morph_family.get(numbered)
        if family:
            return (_game_id(numbered, family),)
        return ('morph:' + plain,) if plain else ()
    ids = []
    for numbered, family in hits:
        gid = _game_id(numbered, family)
        if gid not in ids:
            ids.append(gid)
    return tuple(ids)


def _explode(parts, plain_hits, morph_family) -> tuple[tuple[str, ...], ...]:
    choices = [_ids_for_morph(part, plain_hits, morph_family) or ('morph:' + part,) for part in parts]
    combos = [()]
    for options in choices:
        combos = [prev + (option,) for prev in combos for option in options]
        if len(combos) > 32:
            combos = combos[:32]
            break
    unique = []
    for combo in combos:
        ordered = tuple(sorted(combo))
        if ordered not in unique:
            unique.append(ordered)
    return tuple(unique)


def _sense(lemmas: tuple[str, ...], name: str) -> None:
    for lemma in lemmas:
        _SENSE[fold(lemma)] = (name,)


def _set_readings(lemma: str, names: tuple[str, ...]) -> None:
    """Store alternative one-root readings. Not one compound of all of them."""
    _SENSE[fold(lemma)] = tuple(names)


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

_install_splits(_sense, _set_readings)
