"""Narrow proofs. Each function emits concrete pairs, never a root clique.

Shared roots, alternation sets and compound parts are looked up only as
evidence inside a proof. A pair that fails the structural test is not emitted.
"""

from __future__ import annotations

from games.censorly.lexical.russian.morphology import aspect_of, pos_of, readings

# Allomorphs are evidence for a verbal or diminutive proof, not a licence
# to join every word that carries them.
_VERB_ALTS = (
    frozenset({'клад', 'лож'}),
    frozenset({'вод', 'вед', 'вес'}),
    frozenset({'бег', 'беж'}),
)
_DIMINUTIVE_ALTS = (
    frozenset({'ног', 'нож'}),
    frozenset({'рук', 'руч'}),
)
_PREFIXES = tuple(sorted((
    'пере', 'пред', 'при', 'подо', 'под', 'надо', 'над', 'обо', 'ото', 'от',
    'возо', 'вос', 'воз', 'вы', 'до', 'за', 'изо', 'из', 'ис', 'на', 'не',
    'об', 'по', 'пре', 'про', 'разо', 'раз', 'рас', 'со', 'вз', 'вс',
    'низ', 'нис', 'о', 'с', 'у', 'в',
), key=len, reverse=True))
_ASPECT_ENDS = (
    'ываться', 'иваться', 'ывать', 'ивать', 'аться', 'яться', 'иться',
    'нуть', 'ать', 'ять', 'ить', 'ти', 'чь',
)
# Opposite-aspect endings that are one verb, not two verbs that share a root.
_ASPECT_PAIRS = (
    frozenset({'ывать', 'ить'}),
    frozenset({'ывать', 'нуть'}),
    frozenset({'ивать', 'ить'}),
    frozenset({'ываться', 'иться'}),
    frozenset({'иваться', 'иться'}),
    frozenset({'ать', 'ить'}),
    frozenset({'ать', 'нуть'}),
    frozenset({'ять', 'ить'}),
    frozenset({'аться', 'иться'}),
    frozenset({'яться', 'иться'}),
    frozenset({'ить', 'ти'}),
    frozenset({'иться', 'тись'}),
)
_REL_ADJ = ('овый', 'евый', 'ский', 'ской', 'иный', 'ный', 'ной', 'ний')
_NOUN_ENDS = ('а', 'я', 'о', 'е', 'ь')
# ``-ость``, ``-ение`` and ``-ание`` joined homonyms (сыр/сырость,
# погреб/погребение, устав/уставание). They stay candidate evidence only.
_LEMMA_SUFFIXES = ('овый', 'евый', 'ский', 'ник')
PROOF_ENGINE = 'ru-proofs-2'


def proof_tag(name: str) -> str:
    """Stable id of one automatic rule, including its subtype."""
    return name + ':v1'


class Lexicon:
    """Unambiguous lemma → one numbered root. Built by the compiler."""

    def __init__(self, roots: dict[str, tuple[str, str]], segs: dict[str, tuple[str, ...]]):
        self.roots = roots
        self.segs = segs
        self.lemmas = set(roots)

    def same_root(self, left: str, right: str) -> bool:
        a = self.roots.get(left)
        b = self.roots.get(right)
        return bool(a and b and a == b)

    def plain(self, lemma: str) -> str:
        found = self.roots.get(lemma)
        return found[0] if found else ''


def prove(lex: Lexicon) -> dict[frozenset[str], tuple[str, str]]:
    """Approved pairs for this lexicon. Later rules do not overwrite.

    The value is ``(proof tag, evidence)``. Evidence is for the audit
    artifact; the runtime graph stores only the tag.
    """
    edges: dict[frozenset[str], tuple[str, str]] = {}

    def add(left: str, right: str, proof: str, evidence: str) -> None:
        if not left or not right or left == right:
            return
        key = frozenset((left, right))
        edges.setdefault(key, (proof_tag(proof), evidence))

    _adjective_degree(lex, add)
    _relational_adjective(lex, add)
    _ordinal(lex, add)
    _lemma_suffix(lex, add)
    _verbal_aspect(lex, add)
    _deverbal_noun(lex, add)
    _diminutive(lex, add)
    _place_adverb(lex, add)
    _profession(lex, add)
    return edges


def _adjective_degree(lex: Lexicon, add) -> None:
    """Positive adjective and its -ейший/-айший superlative, same numbered root.

    Spelling pairs such as ``чудной/чудный`` are not degrees and are not emitted.
    ``большой/больший`` is irregular and stays a manual accept.
    """
    for lemma in lex.lemmas:
        for ending in ('ейший', 'айший'):
            if not lemma.endswith(ending):
                continue
            stem = lemma[:-len(ending)]
            if len(stem) < 3 or pos_of(lemma) != 'ADJF':
                continue
            for positive_ending in ('ый', 'ой', 'ий'):
                positive = stem + positive_ending
                if positive not in lex.lemmas or pos_of(positive) != 'ADJF':
                    continue
                if lex.same_root(lemma, positive):
                    add(positive, lemma, f'adjective_degree:{ending}', f'stem={stem};ending={ending}')


def _relational_adjective(lex: Lexicon, add) -> None:
    """Noun ``root+а`` with adjective ``root+ный`` when the numbered root matches.

    ``вода/водный`` and ``пчела/пчелиный`` pass. ``белый/белок`` does not:
    ``белый`` is not one of these relational endings and ``белок`` is not
    ``root+а``.
    """
    for lemma, (plain, _family) in lex.roots.items():
        if len(plain) < 3 or not lemma.startswith(plain):
            continue
        if pos_of(lemma) != 'NOUN':
            continue
        tail = lemma[len(plain):]
        if tail not in _NOUN_ENDS and lemma != plain:
            continue
        for ending in _REL_ADJ:
            adj = plain + ending
            if adj in lex.lemmas and lex.same_root(lemma, adj) and pos_of(adj) == 'ADJF':
                add(
                    lemma, adj, f'relational_adjective:{ending}',
                    f'root={plain};noun={lemma};suffix={ending}',
                )


def _ordinal(lex: Lexicon, add) -> None:
    for lemma, (plain, _family) in lex.roots.items():
        if len(plain) < 3 or pos_of(lemma) != 'NUMR':
            continue
        for ending in ('ый', 'ой', 'ий', 'ая', 'ое'):
            ordinal = plain + ending
            if ordinal not in lex.lemmas or not lex.same_root(lemma, ordinal):
                continue
            if pos_of(ordinal) == 'ANUM':
                add(lemma, ordinal, 'ordinal', f'root={plain};ending={ending}')


def _lemma_suffix(lex: Lexicon, add) -> None:
    """The longer lemma is the shorter lemma plus one listed suffix.

    ``-ость``, ``-ение`` and ``-ание`` are not in the list: the audit found
    repeated homonyms there, so those pairs are not approved edges.
    """
    for lemma in lex.lemmas:
        for suffix in _LEMMA_SUFFIXES:
            if not lemma.endswith(suffix) or len(lemma) - len(suffix) < 3:
                continue
            base = lemma[:-len(suffix)]
            if base in lex.lemmas and lex.same_root(base, lemma):
                add(base, lemma, f'lemma_suffix:{suffix}', f'base={base};suffix={suffix}')


def _verbal_aspect(lex: Lexicon, add) -> None:
    """Same prefix, opposite aspect, allomorph stems. Not every verb of a root.

    ``откладывать/отложить`` share ``от-``. ``вводить/вывести`` do not, and
    ``сложный/складывать`` is not a pair of verbs. ``клад/лож`` may cross
    Kuznetsova families; ``вод/вес`` may not, so weight and leading stay apart.
    """
    buckets: dict[tuple, list[tuple[str, str]]] = {}
    for lemma, (plain, family) in lex.roots.items():
        if len(plain) < 3 or pos_of(lemma) != 'VERB':
            continue
        prefix = _split_root(lemma, plain)
        if prefix is None:
            continue
        ending = lemma[len(prefix) + len(plain):]
        if ending not in _ASPECT_ENDS:
            continue
        aspect = aspect_of(lemma)
        if aspect not in ('impf', 'perf'):
            continue
        row = (lemma, aspect, ending)
        buckets.setdefault(('same', prefix, family, plain), []).append(row)
        for index, group in enumerate(_VERB_ALTS):
            if plain not in group:
                continue
            if group == frozenset({'клад', 'лож'}):
                buckets.setdefault(('cross', prefix, index), []).append(row)
            else:
                buckets.setdefault(('alt', prefix, family, index), []).append(row)
    for key, group in buckets.items():
        if len(group) > 12:
            continue
        mode = key[0]
        plain = key[-1] if mode == 'same' else ''
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if left[1] == right[1]:
                    continue
                endings = frozenset((left[2], right[2]))
                if endings not in _ASPECT_PAIRS:
                    continue
                # ``валить`` and ``валять`` are different verbs. A prefixed
                # ить/ять pair is approved only when the bare ять verb is absent.
                if plain and endings in (frozenset({'ить', 'ять'}), frozenset({'иться', 'яться'})):
                    it = 'иться' if 'иться' in endings else 'ить'
                    yat = 'яться' if 'яться' in endings else 'ять'
                    if plain + it in lex.lemmas and plain + yat in lex.lemmas:
                        continue
                pair = '/'.join(sorted(endings))
                add(
                    left[0], right[0], f'verbal_aspect:{mode}:{pair}',
                    f'mode={mode};endings={pair};root={plain}',
                )


def _deverbal_noun(lex: Lexicon, add) -> None:
    """``бег/бежать``: the noun is the bare allomorph and the verb is unprefixed."""
    by_plain: dict[tuple[str, str], list[str]] = {}
    for lemma, (plain, family) in lex.roots.items():
        if len(plain) < 3:
            continue
        by_plain.setdefault((family, plain), []).append(lemma)
    for (family, plain), lemmas in by_plain.items():
        nouns = [lemma for lemma in lemmas if lemma == plain and pos_of(lemma) == 'NOUN']
        if not nouns:
            continue
        partners: set[str] = set()
        for group in _VERB_ALTS:
            if plain in group:
                partners |= group - {plain}
        for other in partners:
            for verb in by_plain.get((family, other), ()):
                if pos_of(verb) != 'VERB':
                    continue
                if _split_root(verb, other) != '':
                    continue
                if verb[len(other):] not in _ASPECT_ENDS:
                    continue
                for noun in nouns:
                    add(noun, verb, 'deverbal_noun', f'noun={noun};verb={verb};alt={other}')


def _diminutive(lex: Lexicon, add) -> None:
    for lemma, (plain, family) in lex.roots.items():
        if len(plain) < 3 or pos_of(lemma) != 'NOUN':
            continue
        if lemma not in {plain, plain + 'а', plain + 'я', plain + 'о'}:
            continue
        for group in _DIMINUTIVE_ALTS:
            if plain not in group:
                continue
            for other in group:
                if other == plain:
                    continue
                for suffix in ('ка', 'ок', 'ек', 'очка'):
                    derived = other + suffix
                    if derived not in lex.lemmas:
                        continue
                    other_root = lex.roots.get(derived)
                    if not other_root or other_root[1] != family:
                        continue
                    if pos_of(derived) == 'NOUN' and _split_root(lemma, plain) == '':
                        add(lemma, derived, 'diminutive', f'root={plain};alt={other};suffix={suffix}')


def _place_adverb(lex: Lexicon, add) -> None:
    """``в-`` + root + ``е`` next to root + ``о`` or root + ``ость``.

    ``вместе/место`` and ``вскоре/скорость``. ``местность`` is ``-ность``,
    so it is not this pair. The ``в-`` word must be an adverb.
    """
    for lemma, (plain, _family) in lex.roots.items():
        if len(plain) < 4 or not lemma.startswith(plain) or pos_of(lemma) != 'NOUN':
            continue
        for ending in ('о', 'а', 'ость'):
            if lemma != plain + ending:
                continue
            adverb = 'в' + plain + 'е'
            if adverb not in lex.lemmas or not lex.same_root(lemma, adverb):
                continue
            if pos_of(adverb) == 'ADVB':
                add(lemma, adverb, 'place_adverb', f'root={plain};ending={ending}')


def _profession(lex: Lexicon, add) -> None:
    """``пчела/пчеловод``: first root + ``о`` + ``вод``, linked only to that noun.

    ``вода`` is a different numbered root and is not the first component.
    """
    nouns: dict[str, list[str]] = {}
    for noun, (plain, _family) in lex.roots.items():
        if pos_of(noun) != 'NOUN' or _split_root(noun, plain) != '':
            continue
        if noun != plain + 'а' and noun != plain + 'я':
            continue
        # ``дома`` is not the citation form of ``дом``. Skip it.
        if not any(
            item.lemma == noun and item.pos == 'NOUN' and not item.proper
            for item in readings(noun)
        ):
            continue
        nouns.setdefault(plain, []).append(noun)
    for lemma, parts in lex.segs.items():
        if len(parts) < 2 or parts[1] != 'вод':
            continue
        head = parts[0]
        stem = head + 'овод'
        if len(head) < 3 or not lemma.startswith(stem):
            continue
        # ``пчеловод``, ``пчеловодство``. Not ``пароводяной``.
        if lemma[len(stem):] not in ('', 'а', 'ка', 'ный', 'ство', 'ческий'):
            continue
        for noun in nouns.get(head, ()):
            tail = lemma[len(stem):]
            add(noun, lemma, 'profession', f'head={head};tail={tail};source=tikhonov')


def _split_root(lemma: str, root: str) -> str | None:
    if lemma.startswith(root):
        return ''
    for prefix in _PREFIXES:
        if lemma.startswith(prefix + root):
            return prefix
    return None
