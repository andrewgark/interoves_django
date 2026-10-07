"""Target-occurrence inflection identity.

A guess has no sentence, so every live reading stays available. A target
is one token in one sentence. Agreement with a neighbour can select a
single lexical identity. When it cannot, same-lexeme opening stays closed
unless the guess matches every remaining target identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from games.censorly.lexical.core import fold
from games.censorly.tokenize import tokenize_text

_PROPER = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'})
_CASES = ('nomn', 'gent', 'datv', 'accs', 'ablt', 'loct', 'voct')
# One or two governed cases. Wider prepositions are not used as evidence.
_PREP_CASES = {
    'из': frozenset({'gent'}),
    'от': frozenset({'gent'}),
    'до': frozenset({'gent'}),
    'без': frozenset({'gent'}),
    'у': frozenset({'gent'}),
    'для': frozenset({'gent'}),
    'после': frozenset({'gent'}),
    'кроме': frozenset({'gent'}),
    'вокруг': frozenset({'gent'}),
    'против': frozenset({'gent'}),
    'среди': frozenset({'gent'}),
    'из-за': frozenset({'gent'}),
    'из-под': frozenset({'gent'}),
    'к': frozenset({'datv'}),
    'ко': frozenset({'datv'}),
    'при': frozenset({'loct'}),
    'про': frozenset({'accs'}),
    'через': frozenset({'accs'}),
    'над': frozenset({'ablt'}),
    'перед': frozenset({'ablt'}),
    'передо': frozenset({'ablt'}),
    'в': frozenset({'accs', 'loct'}),
    'во': frozenset({'accs', 'loct'}),
    'на': frozenset({'accs', 'loct'}),
    'о': frozenset({'loct', 'accs'}),
    'об': frozenset({'loct', 'accs'}),
}


@dataclass(frozen=True)
class MorphReading:
    lemma: str
    para_id: int
    pos: str
    score: float
    proper: bool
    case: str
    number: str
    gender: str
    animacy: str
    finite: bool
    transitive: bool


@dataclass(frozen=True)
class TargetOccurrence:
    """One token in one place. ``lexemes`` is what inflection may use.

    ``ambiguous`` means more than one lemma survived. A guess then has to
    match every remaining identity, not just one of them.
    """

    surface: str
    lexemes: frozenset[tuple[int, str]]
    ambiguous: bool
    # Noun/verb homographs use the strict rule. Adjective degree ties do not:
    # «большие» is большой or больший, and either citation is a real form.
    conservative: bool
    signals: tuple[str, ...]
    position: int = 0

    @property
    def lemmas(self) -> frozenset[str]:
        return frozenset(lemma for _para, lemma in self.lexemes)


@lru_cache(maxsize=262144)
def guess_inflection_ids(surface: str) -> frozenset[tuple[int, str]]:
    """Context-free guess identities.

    When the typed string is the citation form of one reading and only an
    oblique form of another, keep the citation. ``белка`` is the squirrel's
    dictionary form and also the genitive of ``белок``; the citation wins.
    ``прибыли`` is a citation of neither ``прибыть`` nor ``прибыль``, so
    both readings stay.
    """
    found = list(ungated_readings(surface))
    if not found:
        return frozenset()
    # A typed capital is a name when one exists («Лев»). A typed lowercase
    # word keeps the common noun («лев», «улья»).
    found = _guess_case(surface, found)
    # «улья» is both a place name and the genitive of «улей». The name
    # tie already belongs to the hive, so citation must not steal it.
    found = _name_pool(found)
    folded = fold(surface)
    citation = [item for item in found if item.lemma == folded and not item.proper]
    other = [item for item in found if item.lemma != folded]
    pool = citation if citation and other else found
    return _ids(_proper_pool(pool))


def ungated_readings(surface: str) -> tuple[MorphReading, ...]:
    folded = fold(surface)
    if not folded:
        return ()
    return _ungated(folded)


@lru_cache(maxsize=262144)
def bare_target(surface: str) -> TargetOccurrence:
    """Identity of a surface with no sentence.

    Capitalization is not treated as a mid-sentence name. A capital form
    without neighbours stays on the ordinary name-versus-common split.
    """
    return _analyze_surface(surface, left=[], right=[], sentence_initial=True)


def analyze_phrase(phrase: str) -> list[TargetOccurrence]:
    """Occurrence identity for every content token, in order."""
    tokens = tokenize_text(phrase or '')
    return [item for item in _occurrences(tokens)]


def occurrence_of(phrase: str, surface: str) -> TargetOccurrence | None:
    wanted = fold(surface)
    for item in analyze_phrase(phrase):
        if fold(item.surface) == wanted:
            return item
    return None


def covers_every_identity(
    guess_ids: frozenset[tuple[int, str]],
    target_ids: frozenset[tuple[int, str]],
) -> bool:
    """True when every target identity is also a guess identity."""
    if not target_ids:
        return False
    return target_ids <= guess_ids


def inflection_matches(guess: str, occurrence: TargetOccurrence) -> bool:
    guess_ids = guess_inflection_ids(guess)
    if not guess_ids or not occurrence.lexemes:
        return False
    if occurrence.conservative:
        return covers_every_identity(guess_ids, occurrence.lexemes)
    return bool(guess_ids & occurrence.lexemes)


def _occurrences(tokens: list[dict]) -> list[TargetOccurrence]:
    # Stops include prepositions. Without them, «на скале» has no governor.
    # Only content tokens are puzzle targets.
    word_at = [
        index for index, tok in enumerate(tokens)
        if tok.get('kind') in ('content', 'stop')
    ]
    content_at = [
        index for index, tok in enumerate(tokens)
        if tok.get('kind') == 'content'
    ]
    sentence_start = True
    initial = {}
    for index, tok in enumerate(tokens):
        if tok.get('kind') in ('content', 'stop'):
            initial[index] = sentence_start
            sentence_start = False
        elif tok.get('kind') == 'punct' and (tok.get('surface') or '')[:1] in '.?!…':
            sentence_start = True
    slot_of = {index: slot for slot, index in enumerate(word_at)}
    out = []
    for index in content_at:
        slot = slot_of[index]
        left = [tokens[word_at[pos]]['surface'] for pos in range(slot - 1, -1, -1)]
        right = [tokens[word_at[pos]]['surface'] for pos in range(slot + 1, len(word_at))]
        left = _same_sentence(tokens, word_at, slot, left, direction=-1)
        right = _same_sentence(tokens, word_at, slot, right, direction=1)
        found = _analyze_surface(
            tokens[index]['surface'],
            left=left,
            right=right,
            sentence_initial=initial[index],
        )
        out.append(TargetOccurrence(
            found.surface,
            found.lexemes,
            found.ambiguous,
            found.conservative,
            found.signals,
            index,
        ))
    return out


def _same_sentence(tokens, content_at, position, surfaces, direction) -> list[str]:
    kept = []
    cursor = position
    for surface in surfaces:
        cursor += direction
        previous = min(content_at[position], content_at[cursor])
        following = max(content_at[position], content_at[cursor])
        if any(
            tok.get('kind') == 'punct' and (tok.get('surface') or '')[:1] in '.?!…,;:—–-()«»'
            for tok in tokens[previous + 1:following]
        ):
            break
        kept.append(surface)
        if len(kept) >= 4:
            break
    return kept


def _analyze_surface(
    surface: str,
    *,
    left: list[str],
    right: list[str],
    sentence_initial: bool,
) -> TargetOccurrence:
    found = list(ungated_readings(surface))
    if not found:
        return TargetOccurrence(surface, frozenset(), False, False, ())
    signals: list[str] = []
    found, used = _apply_capital(surface, found, sentence_initial)
    if used:
        signals.append(used)
    else:
        # Name versus common noun, without a score gap that would delete
        # a second common lemma. Syntax decides those.
        found = _name_pool(found)
    found, used = _apply_syntax(found, left, right)
    signals.extend(used)
    # No neighbour evidence: keep the same score window as a context-free guess.
    # Otherwise a weak second paradigm becomes a new opening.
    if not signals:
        found = _proper_pool(found)
    lemmas = {item.lemma for item in found}
    poses = {item.pos for item in found}
    conservative = len(lemmas) > 1 and ('NOUN' in poses or len(poses) > 1)
    return TargetOccurrence(
        surface,
        _ids(found),
        len(lemmas) > 1,
        conservative,
        tuple(signals),
    )


def _guess_case(surface: str, found: list[MorphReading]) -> list[MorphReading]:
    letter = next((ch for ch in surface if ch.isalpha()), '')
    if not letter:
        return found
    proper = [item for item in found if item.proper]
    common = [item for item in found if not item.proper]
    if letter.isupper() and proper:
        return proper
    if letter.islower() and common:
        return common
    return found


def _apply_capital(surface, found, sentence_initial):
    letter = next((ch for ch in surface if ch.isalpha()), '')
    if not letter:
        return found, ''
    proper = [item for item in found if item.proper]
    common = [item for item in found if not item.proper]
    if letter.isupper() and not sentence_initial and proper:
        return proper, 'mid_sentence_capital'
    if letter.islower() and common and proper:
        return common, 'lowercase_common'
    return found, ''


def _apply_syntax(found, left, right):
    signals = []
    left_readings = [ungated_readings(item) for item in left]
    right_readings = [ungated_readings(item) for item in right]
    found, used = _filter_preposition(found, left, left_readings)
    if used:
        signals.append(used)
    found, used = _filter_adjective(found, left_readings, right_readings)
    if used:
        signals.append(used)
    found, used = _filter_noun_complement(found, left_readings)
    if used:
        signals.append(used)
    found, used = _filter_preceding_subject(found, left_readings)
    if used:
        signals.append(used)
    found, used = _filter_pronoun_verb(found, left_readings)
    if used:
        signals.append(used)
    else:
        found, used = _filter_following_verb(found, right_readings)
        if used:
            signals.append(used)
    found, used = _filter_object_case(found, left_readings)
    if used:
        signals.append(used)
    return found, signals


def _filter_preposition(found, left, left_readings):
    prep_at = None
    if left and _is_prep(left_readings[0]):
        prep_at = 0
    elif (
        len(left) >= 2
        and _is_prep(left_readings[1])
        and any(item.pos in {'ADJF', 'PRTF'} for item in left_readings[0])
    ):
        prep_at = 1
    if prep_at is None:
        return found, ''
    cases = _PREP_CASES.get(fold(left[prep_at]), frozenset())
    if not cases:
        return found, ''
    nouns = [item for item in found if item.pos == 'NOUN' and item.case in cases]
    if nouns and len(nouns) < len(found):
        return nouns, 'preposition:' + fold(left[prep_at])
    return found, ''


def _filter_adjective(found, left_readings, right_readings):
    adjectives = [
        item for item in (left_readings[0] if left_readings else ())
        if item.pos in {'ADJF', 'PRTF'}
    ]
    if not adjectives and right_readings:
        adjectives = [item for item in right_readings[0] if item.pos in {'ADJF', 'PRTF'}]
    if not adjectives:
        return found, ''
    nouns = [
        noun for noun in found
        if noun.pos == 'NOUN' and any(_agrees(noun, adj) for adj in adjectives)
    ]
    if not nouns:
        return found, ''
    if len(nouns) < len([item for item in found if item.pos == 'NOUN' or item.finite]):
        return nouns, 'adjective_agreement'
    if any(item.finite for item in found) and nouns:
        return nouns, 'adjective_not_verb'
    return found, ''


def _filter_noun_complement(found, left_readings):
    """«синтез белка»: a noun to the left takes a non-nominative complement.

    A weak noun homograph does not override a clearly better reading.
    «начал вести» stays the infinitive.
    """
    if not left_readings or _best_pos(left_readings[0]) != 'NOUN':
        return found, ''
    kept = [item for item in found if item.pos == 'NOUN' and item.case and item.case != 'nomn']
    if not kept or len(kept) >= len(found):
        return found, ''
    best = max(item.score for item in found)
    if max(item.score for item in kept) < best - 0.15:
        return found, ''
    return kept, 'noun_complement'


def _filter_preceding_subject(found, left_readings):
    """«пришла белка»: the finite verb to the left agrees with a nominative."""
    if not left_readings:
        return found, ''
    verbs = [item for item in left_readings[0] if item.finite]
    if not verbs:
        return found, ''
    kept = [
        item for item in found
        if item.pos == 'NOUN' and item.case == 'nomn'
        and any(_verb_agrees(verb, item) for verb in verbs)
    ]
    if kept and len(kept) < len(found):
        return kept, 'verb_subject'
    return found, ''


def _filter_pronoun_verb(found, left_readings):
    if not left_readings:
        return found, ''
    pronouns = [
        item for item in left_readings[0]
        if item.pos == 'NPRO' and item.case == 'nomn'
    ]
    verbs = [
        item for item in found
        if item.finite and any(_verb_agrees(item, pron) for pron in pronouns)
    ]
    if pronouns and verbs:
        return verbs, 'nominative_pronoun'
    return found, ''


def _filter_following_verb(found, right_readings):
    finite = []
    immediate = False
    for index, readings in enumerate(right_readings[:3]):
        if any(item.finite for item in readings):
            finite = [item for item in readings if item.finite]
            immediate = index == 0
            break
        if readings and not any(item.pos in {'NOUN', 'ADJF', 'PRTF', 'NPRO'} for item in readings):
            break
    if not finite:
        return found, ''
    kept = [item for item in found if not item.finite]
    if immediate:
        subjects = [
            item for item in kept
            if item.pos == 'NOUN' and item.case == 'nomn'
            and any(item.number == verb.number or not item.number or not verb.number for verb in finite)
        ]
        if subjects:
            kept = subjects
    if kept and len(kept) < len(found):
        return kept, 'followed_by_finite_verb'
    return found, ''


def _filter_object_case(found, left_readings):
    verb_at = None
    for index, readings in enumerate(left_readings[:4]):
        if any(item.finite and item.transitive for item in readings):
            verb_at = index
            break
    if verb_at is None:
        return found, ''
    subject = False
    for readings in left_readings[verb_at + 1:verb_at + 4]:
        if any(
            item.pos in {'NOUN', 'NPRO'} and item.case == 'nomn'
            for item in readings
        ):
            subject = True
            break
    if not subject:
        return found, ''
    infinitives = [item for item in found if item.pos == 'VERB' and not item.finite]
    if infinitives:
        best = max(item.score for item in found)
        if max(item.score for item in infinitives) >= best - 0.15:
            # «начал вести»: the infinitive is the object, not a noun homograph.
            return found, ''
    objects = [item for item in found if item.pos == 'NOUN' and item.case != 'nomn']
    if not objects or len(objects) >= len(found):
        return found, ''
    best = max(item.score for item in found)
    if max(item.score for item in objects) < best - 0.15:
        # «служит главным»: the adjective is the real reading.
        return found, ''
    return objects, 'transitive_object'


def _agrees(noun: MorphReading, adj: MorphReading) -> bool:
    if noun.case and adj.case and noun.case != adj.case:
        return False
    if noun.number and adj.number and noun.number != adj.number:
        return False
    if (
        noun.number == 'sing'
        and noun.gender and adj.gender
        and noun.gender != adj.gender
    ):
        return False
    if noun.animacy and adj.animacy and noun.animacy != adj.animacy:
        return False
    return True


def _verb_agrees(verb: MorphReading, pron: MorphReading) -> bool:
    if verb.number and pron.number and verb.number != pron.number:
        return False
    if (
        verb.number == 'sing'
        and verb.gender and pron.gender
        and verb.gender != pron.gender
    ):
        return False
    return True


def _best_pos(readings) -> str:
    if not readings:
        return ''
    return max(readings, key=lambda item: item.score).pos


def _is_prep(readings: tuple[MorphReading, ...]) -> bool:
    return any(item.pos == 'PREP' for item in readings)


def _ids(items) -> frozenset[tuple[int, str]]:
    return frozenset((item.para_id, item.lemma) for item in items)


def _name_pool(found: list[MorphReading]) -> list[MorphReading]:
    common = [item for item in found if not item.proper]
    names = [item for item in found if item.proper]
    if common and names:
        common_best = max(item.score for item in common)
        name_best = max(item.score for item in names)
        if common_best >= 0.2 and common_best >= name_best - 0.05:
            return common
        if name_best > common_best + 0.05:
            return names
    return common or found


def _proper_pool(found: list[MorphReading]) -> list[MorphReading]:
    found = _name_pool(found)
    if not found:
        return []
    best = max(item.score for item in found)
    return [item for item in found if item.score >= best - 0.15 and item.score >= 0.05]


@lru_cache(maxsize=262144)
def _ungated(folded: str) -> tuple[MorphReading, ...]:
    from games.matcher.norm_matcher import MORPH_ANALYZER

    parsed = []
    for parse in MORPH_ANALYZER.parse(folded):
        if not parse.methods_stack:
            continue
        if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
            continue
        _word, para_id, _idx = parse.methods_stack[0][1:]
        grams = parse.tag.grammemes
        score = float(parse.score)
        if score < 0.05:
            continue
        parsed.append(MorphReading(
            lemma=fold(parse.normal_form),
            para_id=int(para_id),
            pos=_pos(grams),
            score=score,
            proper=bool(grams & _PROPER),
            case=_one(grams, _CASES),
            number=_one(grams, ('sing', 'plur')),
            gender=_one(grams, ('masc', 'femn', 'neut')),
            animacy=_one(grams, ('anim', 'inan')),
            finite='VERB' in grams and bool(grams & {'past', 'pres', 'futr'}),
            transitive='tran' in grams,
        ))
    return tuple(parsed)


def _pos(grams: set[str]) -> str:
    if 'PREP' in grams:
        return 'PREP'
    if 'NPRO' in grams:
        return 'NPRO'
    if 'PRTF' in grams or 'PRTS' in grams:
        return 'PRTF'
    if grams & {'VERB', 'INFN'}:
        return 'VERB'
    if grams & {'ADJF', 'ADJS'}:
        return 'ADJF'
    if 'NOUN' in grams:
        return 'NOUN'
    if 'ADVB' in grams:
        return 'ADVB'
    return ''


def _one(grams: set[str], names: tuple[str, ...]) -> str:
    for name in names:
        if name in grams:
            return name
    return ''
