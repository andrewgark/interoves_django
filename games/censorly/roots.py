"""Pairwise cognates for Цензурки.

The guess path still matches lemmas only. ``opens_with`` is the candidate
for a later flag that would also open однокоренные words.

A Kuznetsova or Tikhonov root is only a candidate. Two words open together
when the pair itself is safe: one lexeme, a closed school family, a
word-initial root inside a small nest, a whitelisted modern alternation,
or a compound whose component is an unambiguous short root.

Shared root id is not transitive. A~B and B~C do not open A with C.
Numbered roots stay apart unless a whitelist says those allomorphs are
the same modern stem. A family larger than ``FAMILY_CAP`` is not linked
by "same morph"; the cap does not block a whitelisted pair inside it
(вод/вес, клад/лож).

Stress is kept as part of the lexeme. стро́ить and строи́ть stay apart.
An ambiguous pymorphy form (прибыли) is not expanded past its readings.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from games.censorly.normalize import normalize_surface

_INDEX_PATH = Path(__file__).resolve().parent / 'data' / 'cognate_roots.tsv.gz'
# Same-morph pairs are refused in a nest bigger than this. Whitelisted
# alternations still match inside those nests. Size is not a match key.
FAMILY_CAP = 120
_SUPERSCRIPTS = str.maketrans('', '', '¹²³⁴⁵⁶⁷⁸⁹⁰')
_PROPER = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'})
_NOTE_RE = re.compile(r'\s*[(\[].*?[)\]]\s*')

# Closed school sets. Membership is pairwise and is not extended by a
# dictionary root. Explicit unlinks win over these.
_LINK_FAMILIES = (
    frozenset({'он', 'она', 'оно', 'они'}),
    frozenset({'один', 'единый', 'единственный'}),
    frozenset({'день', 'дневный', 'дневной'}),
)

# Final guardrail for pairs a school reader does not feel as one root,
# including cases the dictionary numbers as a single morph.
_UNLINK = frozenset({
    frozenset({'воздух', 'дышать'}),
    frozenset({'достаточный', 'состав'}),
    frozenset({'достаточный', 'становиться'}),
    frozenset({'достаточный', 'представлять'}),
    frozenset({'достаточный', 'остаться'}),
    frozenset({'необходимый', 'выходить'}),
    frozenset({'образ', 'раз'}),
    frozenset({'образ', 'сразу'}),
    frozenset({'цвет', 'цветковый'}),
    frozenset({'один', 'однако'}),
    frozenset({'личинка', 'отличие'}),
    frozenset({'строить', 'три'}),
    frozenset({'верхний', 'совершать'}),
    frozenset({'печка', 'обеспечить'}),
    frozenset({'брачный', 'забраться'}),
    frozenset({'самый', 'самец'}),
    frozenset({'слово', 'условие'}),
    frozenset({'год', 'погода'}),
    frozenset({'запас', 'опасность'}),
    frozenset({'равный', 'уровень'}),
    frozenset({'длина', 'длиться'}),
    frozenset({'друг', 'другой'}),
    frozenset({'полукольцо', 'поляризовать'}),
})

# Dictionary failed to number these senses apart. A word on the left
# never opens a word on the right. Same-side pairs still need a safe rule.
_SENSE_SPLITS = (
    (
        frozenset({
            'бесцветность', 'бесцветный', 'обесцветить', 'обесцветиться',
            'обесцвечение', 'обесцвеченный', 'обесцвечивание', 'обесцвечивать',
            'обесцвечиваться', 'отцветить', 'отцвечивать', 'отцвечиваться',
            'подцветить', 'подцветка', 'подцвечивать', 'расцветить',
            'расцветиться', 'расцветка', 'расцветчик', 'расцвечивание',
            'расцвечивать', 'расцвечиваться', 'цвет', 'цветастость', 'цветастый',
            'цветистость', 'цветистый', 'цветить', 'цветной', 'цветность',
            'цветовой',
        }),
        frozenset({
            'бесцветковые', 'выцвелый', 'выцвести', 'выцветание', 'выцветать',
            'доцвести', 'доцветать', 'зацвести', 'зацветание', 'зацветать',
            'отцвести', 'отцветать', 'прицветник', 'процвести', 'процветание',
            'процветать', 'расцвести', 'расцвет', 'расцветание', 'расцветать',
            'соцветие', 'цвель', 'цвести', 'цветение', 'цветень', 'цветик',
            'цветковый', 'цветневой', 'цветник', 'цветниковый', 'цветничок',
            'цветок', 'цветочек', 'цветочник', 'цветочница', 'цветочный',
            'цветуха', 'цветущий', 'цветы',
        }),
    ),
    (
        frozenset({'самый'}),
        frozenset({'сам', 'самец', 'самка', 'самость'}),
    ),
)

# Modern allomorphs. Same-family groups may cross prefixes only for the
# verb stem вод/вед/вес. Cross-family groups require the same prefix.
_SAME_FAMILY_ALT = (
    frozenset({'вод', 'вед', 'вес'}),
    frozenset({'бег', 'беж'}),
    frozenset({'ног', 'нож'}),
    frozenset({'рук', 'руч'}),
    frozenset({'ден', 'дн', 'днев'}),
)
_CROSS_FAMILY_ALT = (
    frozenset({'клад', 'лож'}),
)
_VERB_ANY_PREFIX = frozenset({'вод', 'вед', 'вес'})
_PREFIXES = tuple(sorted((
    'пере', 'пред', 'при', 'подо', 'под', 'надо', 'над', 'обо', 'ото', 'от',
    'возо', 'вос', 'воз', 'вы', 'до', 'за', 'изо', 'из', 'ис', 'на', 'не',
    'об', 'по', 'пре', 'про', 'разо', 'раз', 'рас', 'со', 'вз', 'вс',
    'низ', 'нис', 'о', 'с', 'у', 'в',
), key=len, reverse=True))
_ADJ_ENDS = ('ий', 'ый', 'ой', 'ая', 'ое', 'ее', 'ые', 'ие')
# Tikhonov writes "мед" for both honey and copper. Only a surface that
# actually begins with the honey-bearer stem is safe; медосмотр and медь are not.
_AMBIGUOUS_COMPOUNDS = (
    ('мед', 'медонос'),
)

_index: dict[str, tuple['_Variant', ...]] | None = None
_segments: dict[str, tuple[str, ...]] | None = None
_plain_families: dict[str, frozenset[str]] | None = None


@dataclass(frozen=True)
class _Root:
    raw: str
    plain: str
    family: str
    size: int


@dataclass(frozen=True)
class _Variant:
    sig: str
    root: _Root | None


@dataclass(frozen=True)
class _Entry:
    roots: tuple[_Root, ...]
    segs: tuple[str, ...]


def opens_with(guess: str, target: str) -> bool:
    """True when a guess should open the target word.

    Same unambiguous lexeme, or a pairwise safe cognate. Ambiguous
    morphology and stress homographs are not expanded by root.
    """
    guess_sig = _stress_sig(guess)
    target_sig = _stress_sig(target)
    left = normalize_surface(guess)
    right = normalize_surface(target)
    if not left or not right:
        return False
    if guess_sig and target_sig and guess_sig != target_sig and left == right:
        return False
    if left == right:
        return True
    left_lemmas = _content_lemmas(left)
    right_lemmas = _content_lemmas(right)
    if left_lemmas & right_lemmas:
        return True
    if len(left_lemmas) != 1 or len(right_lemmas) != 1:
        # прибыли has two unrelated readings, so it stops here.
        # дневной's two readings are the same adjective and stay linkable.
        return _closed_family(left_lemmas, right_lemmas)
    left_lemma = next(iter(left_lemmas))
    right_lemma = next(iter(right_lemmas))
    pair = frozenset((left_lemma, right_lemma))
    if pair in _UNLINK or _sense_blocked(left_lemma, right_lemma):
        return False
    if any(pair <= family for family in _LINK_FAMILIES):
        return True
    left_entry = _lookup(left_lemma, guess_sig if left == left_lemma else '')
    right_entry = _lookup(right_lemma, target_sig if right == right_lemma else '')
    if left_entry is None or right_entry is None:
        return False
    return _safe_pair(left_lemma, left_entry, right_lemma, right_entry)


def open_reason(guess: str, target: str) -> str:
    """Why ``opens_with`` is true, or ``''`` when it is false.

    Diagnostic only. Checks run in the same order as ``opens_with`` and
    do not change that result. Labels:

    ``identical``, ``same_lemma``, ``closed_family``, ``adjective_degree``,
    ``generic_root:<morph>:<family_size>``, ``alternation:<a>/<b>``,
    ``compound``, ``marked_compound``.
    """
    guess_sig = _stress_sig(guess)
    target_sig = _stress_sig(target)
    left = normalize_surface(guess)
    right = normalize_surface(target)
    if not left or not right:
        return ''
    if guess_sig and target_sig and guess_sig != target_sig and left == right:
        return ''
    if left == right:
        return 'identical'
    left_lemmas = _content_lemmas(left)
    right_lemmas = _content_lemmas(right)
    if left_lemmas & right_lemmas:
        return 'same_lemma'
    if len(left_lemmas) != 1 or len(right_lemmas) != 1:
        return 'closed_family' if _closed_family(left_lemmas, right_lemmas) else ''
    left_lemma = next(iter(left_lemmas))
    right_lemma = next(iter(right_lemmas))
    pair = frozenset((left_lemma, right_lemma))
    if pair in _UNLINK or _sense_blocked(left_lemma, right_lemma):
        return ''
    if any(pair <= family for family in _LINK_FAMILIES):
        return 'closed_family'
    left_entry = _lookup(left_lemma, guess_sig if left == left_lemma else '')
    right_entry = _lookup(right_lemma, target_sig if right == right_lemma else '')
    if left_entry is None or right_entry is None:
        return ''
    return _safe_reason(left_lemma, left_entry, right_lemma, right_entry)


def compile_index(
    kuznetsova_lemmas: Path,
    kuznetsova_groups: Path,
    tikhonov_lemmas: Path,
    dest: Path | None = None,
) -> Path:
    """Write the gzipped candidate index. Returns the destination path.

    The file stores numbered roots and Tikhonov segments. It does not
    store a key that would open a whole family.
    """
    global _index, _segments, _plain_families
    dest = dest or _INDEX_PATH
    redirect, morph_to_id = _read_groups(kuznetsova_groups)
    raw_rows: list[tuple[str, str, str, str]] = []
    for line in _lines(kuznetsova_lemmas):
        lemma, root = line.split('\t', 1)
        base = _NOTE_RE.sub('', lemma).strip()
        key = _norm(base)
        raw = _norm(root.strip())
        if key and raw:
            raw_rows.append((key, _stress_sig(base), raw, _canon(raw, redirect, morph_to_id)))

    sizes: dict[str, set[str]] = defaultdict(set)
    for key, _sig, _raw, family in raw_rows:
        sizes[family].add(key)
    family_size = {family: len(lemmas) for family, lemmas in sizes.items()}

    by_sig: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for key, sig, raw, family in raw_rows:
        by_sig[(key, sig)].add((raw, family))

    tik: dict[str, list[str]] = {}
    for line in _lines(tikhonov_lemmas):
        lemma, seg = line.split('\t', 1)
        key = _norm(_NOTE_RE.sub('', lemma).strip())
        if not key:
            continue
        roots = []
        for part in seg.split('/'):
            if ':' not in part:
                continue
            morph, kind = part.rsplit(':', 1)
            if kind == 'ROOT':
                morph_n = _plain(morph)
                if morph_n and morph_n not in roots:
                    roots.append(morph_n)
        if roots:
            tik[key] = roots

    kuz_lemmas = {key for key, _sig in by_sig}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, 'wt', encoding='utf-8') as handle:
        for key, sig in sorted(by_sig):
            roots = by_sig[(key, sig)]
            if len(roots) != 1:
                handle.write(f'A\t{key}\t{sig}\n')
                continue
            raw, family = next(iter(roots))
            handle.write(f'K\t{key}\t{sig}\t{raw}\t{family}\t{family_size[family]}\n')
        for key in sorted(tik):
            if key in kuz_lemmas:
                continue
            handle.write('T\t' + key + '\t' + ','.join(tik[key]) + '\n')
    _index = None
    _segments = None
    _plain_families = None
    return dest


def _safe_reason(left: str, left_entry: _Entry, right: str, right_entry: _Entry) -> str:
    if _adjective_degree(left, right):
        return 'adjective_degree'
    for lroot in left_entry.roots:
        for rroot in right_entry.roots:
            if _same_visible_morph(left, lroot, right, rroot):
                return f'generic_root:{lroot.plain}:{lroot.size}'
            if _alternation(left, lroot, right, rroot):
                return f'alternation:{lroot.plain}/{rroot.plain}'
    if _compound(left, left_entry, right, right_entry) or _compound(right, right_entry, left, left_entry):
        return 'compound'
    if _marked_compound(left, left_entry, right) or _marked_compound(right, right_entry, left):
        return 'marked_compound'
    return ''


def _safe_pair(left: str, left_entry: _Entry, right: str, right_entry: _Entry) -> bool:
    if _adjective_degree(left, right):
        return True
    for lroot in left_entry.roots:
        for rroot in right_entry.roots:
            if _same_visible_morph(left, lroot, right, rroot):
                return True
            if _alternation(left, lroot, right, rroot):
                return True
    if _compound(left, left_entry, right, right_entry):
        return True
    if _compound(right, right_entry, left, left_entry):
        return True
    if _marked_compound(left, left_entry, right):
        return True
    if _marked_compound(right, right_entry, left):
        return True
    return False


def _same_visible_morph(left: str, lroot: _Root, right: str, rroot: _Root) -> bool:
    if lroot.plain != rroot.plain or lroot.family != rroot.family:
        return False
    morph = lroot.plain
    if len(morph) < 3 or lroot.size > FAMILY_CAP:
        return False
    return _narrow_prefix(left, morph) is not None and _narrow_prefix(right, morph) is not None


def _narrow_prefix(lemma: str, morph: str) -> str | None:
    """Prefix only when the morph is still obvious at the start of the word."""
    if lemma.startswith(morph):
        return ''
    if lemma.startswith('в' + morph):
        return 'в'
    if lemma.startswith('о' + morph):
        rest = lemma[1 + len(morph):]
        if rest.startswith(('ени', 'ани', 'яни')):
            return 'о'
    return None


def _alternation(left: str, lroot: _Root, right: str, rroot: _Root) -> bool:
    if lroot.plain == rroot.plain:
        return False
    pair = frozenset((lroot.plain, rroot.plain))
    min_len = 2 if pair <= frozenset({'ден', 'дн', 'днев'}) else 3
    if len(lroot.plain) < min_len or len(rroot.plain) < min_len:
        return False
    left_prefix = _split_morph(left, lroot.plain)
    right_prefix = _split_morph(right, rroot.plain)
    if left_prefix is None or right_prefix is None:
        return False
    for group in _SAME_FAMILY_ALT:
        if pair <= group and lroot.family == rroot.family:
            if left_prefix == right_prefix:
                return True
            if pair <= _VERB_ANY_PREFIX and _pos(left) == 'VERB' and _pos(right) == 'VERB':
                return True
    for group in _CROSS_FAMILY_ALT:
        if pair <= group and left_prefix == right_prefix:
            return True
    return False


def _split_morph(lemma: str, morph: str) -> str | None:
    if len(morph) < 2:
        return None
    if lemma.startswith(morph):
        return ''
    for prefix in _PREFIXES:
        if lemma.startswith(prefix + morph):
            return prefix
    return None


def _marked_compound(host: str, host_entry: _Entry, base: str) -> bool:
    """A compound whose root spelling is a known homonym, matched by stem."""
    for root, stem in _AMBIGUOUS_COMPOUNDS:
        if base == root and root in host_entry.segs and host.startswith(stem):
            return True
    return False


def _compound(_host: str, host_entry: _Entry, base: str, base_entry: _Entry) -> bool:
    if len(host_entry.segs) < 2 or len(base_entry.roots) != 1:
        return False
    root = base_entry.roots[0]
    if root.plain not in host_entry.segs or len(root.plain) < 3:
        return False
    if root.size > FAMILY_CAP:
        return False
    families = _load_plain_families().get(root.plain, frozenset())
    if len(families) != 1:
        return False
    return base.startswith(root.plain)


def _adjective_degree(left: str, right: str) -> bool:
    left_stem = _adj_stem(left)
    right_stem = _adj_stem(right)
    if not left_stem or left_stem != right_stem:
        return False
    return _pos(left) == 'ADJF' and _pos(right) == 'ADJF'


def _adj_stem(lemma: str) -> str:
    for ending in _ADJ_ENDS:
        if lemma.endswith(ending) and len(lemma) - len(ending) >= 4:
            return lemma[:-len(ending)]
    return ''


def _closed_family(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    for family in _LINK_FAMILIES:
        if left <= family and right <= family:
            return True
    return False


def _sense_blocked(left: str, right: str) -> bool:
    for group_a, group_b in _SENSE_SPLITS:
        if (left in group_a and right in group_b) or (left in group_b and right in group_a):
            return True
    return False


def _lookup(lemma: str, sig: str) -> _Entry | None:
    variants, segments = _load_index()
    segs = segments.get(lemma, ())
    found = variants.get(lemma)
    if not found:
        return _Entry((), segs)
    if sig:
        found = tuple(item for item in found if item.sig == sig)
        if len(found) != 1:
            return None
    elif any(item.root is None for item in found):
        return None
    else:
        roots = {item.root for item in found}
        if len(roots) != 1:
            return None
        found = found[:1]
    variant = found[0]
    if variant.root is None:
        return None
    return _Entry((variant.root,), segs)


def _load_index() -> tuple[dict[str, tuple[_Variant, ...]], dict[str, tuple[str, ...]]]:
    global _index, _segments, _plain_families
    if _index is not None and _segments is not None and _plain_families is not None:
        return _index, _segments
    grouped: dict[str, list[_Variant]] = defaultdict(list)
    segs: dict[str, tuple[str, ...]] = {}
    plain_families: dict[str, set[str]] = defaultdict(set)
    with gzip.open(_INDEX_PATH, 'rt', encoding='utf-8') as handle:
        for line in handle:
            kind, payload = line.rstrip('\n').split('\t', 1)
            if kind == 'A':
                lemma, sig = payload.split('\t', 1)
                grouped[lemma].append(_Variant(sig, None))
            elif kind == 'K':
                lemma, sig, raw, family, size = payload.split('\t', 4)
                plain = _plain(raw)
                root = _Root(raw, plain, family, int(size))
                grouped[lemma].append(_Variant(sig, root))
                plain_families[plain].add(family)
            elif kind == 'T':
                lemma, morphs = payload.split('\t', 1)
                segs[lemma] = tuple(part for part in morphs.split(',') if part)
    _index = {lemma: tuple(items) for lemma, items in grouped.items()}
    _segments = segs
    _plain_families = {plain: frozenset(families) for plain, families in plain_families.items()}
    return _index, _segments


def _load_plain_families() -> dict[str, frozenset[str]]:
    _load_index()
    return _plain_families or {}


@lru_cache(maxsize=8192)
def _content_lemmas(surface: str) -> frozenset[str]:
    """Dictionary lemmas of a surface, without a single lucky first parse.

    Proper-name readings lose to a common noun at the same score, so
    улей/улья stay one lexeme. Two live readings (прибыль and прибыть)
    are both kept, and the caller then refuses root expansion.
    """
    from games.matcher.norm_matcher import MORPH_ANALYZER

    if not surface or not _cyrillic(surface) or '-' in surface:
        return frozenset({surface}) if surface else frozenset()
    parses = []
    for parse in MORPH_ANALYZER.parse(surface):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        parses.append(parse)
    if not parses:
        return frozenset({surface})
    common = [parse for parse in parses if not (parse.tag.grammemes & _PROPER)]
    pool = common or parses
    best = max(parse.score for parse in pool)
    kept = [parse for parse in pool if parse.score >= best - 0.15 and parse.score >= 0.05]
    lemmas = {normalize_surface(parse.normal_form) for parse in kept}
    return frozenset(lemmas) or frozenset({surface})


@lru_cache(maxsize=8192)
def _pos(lemma: str) -> str:
    from games.matcher.norm_matcher import MORPH_ANALYZER

    best = None
    for parse in MORPH_ANALYZER.parse(lemma):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        if parse.tag.grammemes & _PROPER:
            continue
        if best is None or parse.score > best.score:
            best = parse
    if best is None:
        return ''
    grams = best.tag.grammemes
    if grams & {'VERB', 'INFN'}:
        return 'VERB'
    if grams & {'ADJF', 'ADJS'}:
        return 'ADJF'
    return ''


def _read_groups(path: Path):
    redirect: dict[str, str] = {}
    morph_to_id: dict[str, str] = {}
    for line in _lines(path):
        if '→' in line:
            left, right = (_norm(part.strip()) for part in line.split('→', 1))
            if left and right:
                redirect[left] = right
            continue
        parts = []
        for part in line.split('/'):
            part_n = _norm(part.strip())
            if part_n and part_n not in parts:
                parts.append(part_n)
        if not parts:
            continue
        family = '|'.join(parts)
        for part in parts:
            morph_to_id[part] = family
    return redirect, morph_to_id


def _canon(root: str, redirect: dict[str, str], morph_to_id: dict[str, str]) -> str:
    seen: set[str] = set()
    current = root
    while current in redirect and current not in seen:
        seen.add(current)
        current = redirect[current]
    return morph_to_id.get(current, current)


def _lines(path: Path):
    text = path.read_text(encoding='utf-8')
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield line


def _stress_sig(text: str) -> str:
    folded = unicodedata.normalize('NFC', text or '')
    positions = []
    base = 0
    for char in folded:
        if unicodedata.category(char) == 'Mn':
            positions.append(str(base - 1))
        else:
            base += 1
    return ','.join(positions)


def _norm(text: str) -> str:
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(char for char in folded if unicodedata.category(char) != 'Mn')
    return folded.lower().replace('ё', 'е').strip()


def _plain(morph: str) -> str:
    plain = _norm(morph).translate(_SUPERSCRIPTS)
    return plain.replace('(j)', '').replace('(', '').replace(')', '')


def _cyrillic(text: str) -> bool:
    return bool(text) and all('а' <= char <= 'я' for char in text)
