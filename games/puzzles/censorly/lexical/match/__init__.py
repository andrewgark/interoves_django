"""One-pass article index for the gameplay resolver.

A guess is analyzed once. Each target token is analyzed once per article
and then reused. Comparison is the same existential check as ``explain``.
"""

from __future__ import annotations

from array import array
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import Future
from functools import lru_cache
from threading import Lock, RLock

from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.rootbank import families_of, structures_of
from games.puzzles.censorly.lexical.russian.reading_overrides import (
    CITY_ABBR_CONTEXT,
    LANGUAGE_LABEL_CONTEXTS,
    NEM_GERMAN_CONTEXT,
    NEM_NONPRONOUN_CONTEXT,
    NEM_PRONOUN_CONTEXT,
    N_ERA_CONTEXT,
    PAGE_ABBR_CONTEXT,
    PO_SOFTWARE_CONTEXT,
    STREET_ABBR_CONTEXT,
    YEAR_ABBR_CONTEXT,
    guess_lemma_override,
    proper_name_root_lemma,
    target_lemma_override,
    target_readings,
)
from games.puzzles.censorly.lexical.semantics import (
    _alias,
    _ALIASES,
    _grammar,
    _GRAMMAR,
    _PROPER_COMPONENT_BY_WORD,
    _roots,
    _russian,
    guess_readings_of,
    initially_open,
    label_lemmas,
    preferred_readings_of,
    readings_of,
)
from games.puzzles.censorly.lexical.splits import SENSE_COMPATIBLE

_REJECT_CAP = 200
_DIGIT_CARDINAL_LEMMAS = {
    '0': 'ноль', '1': 'один', '2': 'два', '3': 'три',
    '4': 'четыре', '5': 'пять', '6': 'шесть', '7': 'семь',
    '8': 'восемь', '9': 'девять', '10': 'десять',
    '11': 'одиннадцать', '12': 'двенадцать', '13': 'тринадцать',
    '14': 'четырнадцать', '15': 'пятнадцать', '16': 'шестнадцать',
    '17': 'семнадцать', '18': 'восемнадцать', '19': 'девятнадцать',
    '20': 'двадцать', '30': 'тридцать', '40': 'сорок',
    '50': 'пятьдесят', '60': 'шестьдесят', '70': 'семьдесят',
    '80': 'восемьдесят', '90': 'девяносто', '100': 'сто',
    '200': 'двести', '300': 'триста', '400': 'четыреста',
    '500': 'пятьсот', '600': 'шестьсот', '700': 'семьсот',
    '800': 'восемьсот', '900': 'девятьсот', '1000': 'тысяча',
}


def _numeric_root_match(guess: _Pack, target: _Pack) -> bool:
    """Allow a digit/cardinal root match despite «один» service status."""
    if not (guess.fold.isdigit() or target.fold.isdigit()):
        return False
    digit = guess.fold if guess.fold.isdigit() else target.fold
    other = target if digit == guess.fold else guess
    cardinal = _DIGIT_CARDINAL_LEMMAS.get(digit)
    if not cardinal or not any(item.lemma == cardinal for item in other.reads):
        return False
    common = guess.roots & target.roots
    return bool(common)


def _root_sense_compatible(guess_structures, target_structures) -> bool:
    """Match reviewed compatible senses only inside the same root family."""
    for guess_structure in guess_structures:
        guess_roots = {
            part for part in guess_structure if not part.startswith('sense:')
        }
        guess_senses = {
            part for part in guess_structure if part.startswith('sense:')
        }
        if not guess_roots or not guess_senses:
            continue
        for target_structure in target_structures:
            target_roots = {
                part for part in target_structure if not part.startswith('sense:')
            }
            target_senses = {
                part for part in target_structure if part.startswith('sense:')
            }
            if (
                guess_roots == target_roots
                and frozenset(guess_senses | target_senses) in SENSE_COMPATIBLE
            ):
                return True
    return False


def _group_ids_by_word(groups):
    members = defaultdict(list)
    for group_id, group in enumerate(groups):
        for word in group:
            members[word].append(group_id)
    return {word: tuple(group_ids) for word, group_ids in members.items()}


_ALIAS_GROUPS_BY_WORD = _group_ids_by_word(_ALIASES)
_GRAMMAR_GROUPS_BY_WORD = _group_ids_by_word(_GRAMMAR)
_ARTICLE_ANALYSIS_CACHE_MAXSIZE = 4
_ARTICLE_ANALYSIS_CACHE_MAX_SURFACES = 12000
_ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_SURFACES = 6000
_ARTICLE_ANALYSIS_CACHE_MAX_TOKENS = 40000
_ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_TOKENS = 20000
_article_analysis_cache = OrderedDict()
_article_analysis_cache_surfaces = 0
_article_analysis_cache_tokens = 0
_article_analysis_cache_lock = RLock()
_article_analysis_inflight: dict[tuple, Future] = {}
_LETTER_NAME_LEMMAS = {'е': 'е', 'ш': 'ш'}

# Narrow, reviewed lexical links reported missing in Censorly gameplay.
# These lemma pairs are handled separately from root-family matching.
_REVIEWED_LEXICAL_LINKS = frozenset({
    frozenset({'фамилия', 'фамильный'}),
    frozenset({'поменять', 'сменить'}),
    frozenset({'долгоживущий', 'долгоживучий'}),
})


class _Pack:
    __slots__ = (
        'surface', 'fold', 'service', 'russian', 'lexemes', 'roots',
        'root_ids', 'reads', 'families', 'contextual_reading',
    )

    def __init__(
        self, surface: str, *, include_pronouns: bool = True,
        article_lemma: str | None = None,
    ):
        self.surface = surface
        self.fold = fold(surface)
        self.contextual_reading = article_lemma or ''
        self.service = initially_open(surface, include_pronouns=include_pronouns)
        if (self.fold, article_lemma) == ('кому', 'кома'):
            self.service = False
        self.reads = preferred_readings_of(surface)
        # OpenCorpora/pymorphy also tags the standalone letter name «Е» as a
        # rare present-tense form of «быть». Treat that token as the letter
        # sense installed in semantic_splits, so ordinary forms of «быть» do
        # not reveal it through the spurious homographic reading.
        letter_lemma = _LETTER_NAME_LEMMAS.get(self.fold)
        if letter_lemma:
            self.reads = tuple(item for item in self.reads if item.lemma == letter_lemma)
        contextual = False
        override_lemmas = ()
        proper_name_lemma = proper_name_root_lemma(surface)
        if proper_name_lemma:
            self.reads = ()
            contextual = True
        elif article_lemma:
            override_lemmas = target_lemma_override(surface, article_lemma)
            if override_lemmas:
                self.reads = tuple(
                    item
                    for lemma in override_lemmas
                    for item in preferred_readings_of(lemma)
                    if fold(item.lemma) == fold(lemma)
                )
                contextual = True
            else:
                self.reads, contextual = target_readings(
                    surface, article_lemma, self.reads,
                )
        self.russian = _russian(surface, 'ru')
        if proper_name_lemma:
            self.russian = True
        if override_lemmas:
            # Synthetic proper-name overrides can be morphologically
            # unknown (for example «Троей»).  They are still Russian
            # gameplay tokens because the override supplies their identity.
            self.russian = True
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        # Root equality uses citation-preferred readings on both sides.  The
        # broader noun alternatives remain in ``reads`` for SAME_LEXEME.
        # An explicit article reading takes precedence over citation-form
        # preference: «лет» selected as «год» must not fall back to «лёт».
        root_reads = self.reads if contextual else guess_readings_of(surface)
        if proper_name_lemma:
            root_reads = ()
        elif article_lemma:
            override_lemmas = target_lemma_override(surface, article_lemma)
            if override_lemmas:
                root_reads = tuple(
                    item
                    for lemma in override_lemmas
                    for item in guess_readings_of(lemma)
                )
            else:
                root_reads, _ = target_readings(
                    surface, article_lemma, root_reads,
                )
        if proper_name_lemma:
            self.roots = set(structures_of(proper_name_lemma))
        elif override_lemmas:
            self.roots = {
                structure
                for lemma in override_lemmas
                for structure in structures_of(lemma)
            }
        else:
            self.roots = _roots(
                root_reads, surface, include_surface=not contextual,
            )
        self.root_ids = frozenset(_root_ids(self.roots))
        families = set(families_of(self.fold))
        for item in self.reads:
            families.update(families_of(item.lemma))
        self.families = families


class _GuessPack(_Pack):
    """Article-independent pack with citation-form preference."""

    __slots__ = ('lexemes_by_fold',)

    def __init__(
        self, surface: str, *, include_pronouns: bool = True,
        article_lemma: str | None = None,
    ):
        super().__init__(surface, include_pronouns=include_pronouns)
        lexemes_by_fold = defaultdict(set)
        for item in preferred_readings_of(surface):
            lexemes_by_fold[fold(item.lemma)].add((item.para_id, item.lemma))
        self.lexemes_by_fold = {
            lemma_fold: frozenset(lexemes)
            for lemma_fold, lexemes in lexemes_by_fold.items()
        }
        # Use the same narrow citation-form preference as the target pack.
        guess_override = guess_lemma_override(surface)
        if guess_override:
            self.russian = True
        self.reads = (
            tuple(
                item
                for lemma in guess_override
                for item in guess_readings_of(lemma)
                if fold(item.lemma) == fold(lemma)
            )
            if guess_override else guess_readings_of(surface)
        )
        # Keep the standalone letter-name correction when rebuilding the
        # citation-form guess pack; otherwise article scans can resurrect the
        # spurious «Е» -> «быть» reading filtered by _Pack.
        letter_lemma = _LETTER_NAME_LEMMAS.get(self.fold)
        if letter_lemma:
            self.reads = tuple(item for item in self.reads if item.lemma == letter_lemma)
        contextual = False
        override_lemmas = ()
        if article_lemma:
            override_lemmas = target_lemma_override(surface, article_lemma)
            if override_lemmas:
                self.reads = tuple(
                    item
                    for lemma in override_lemmas
                    for item in guess_readings_of(lemma)
                )
                contextual = True
            else:
                self.reads, contextual = target_readings(
                    surface, article_lemma, self.reads,
                )
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        if guess_override:
            self.roots = {
                structure
                for lemma in guess_override
                for structure in structures_of(lemma)
            }
        else:
            self.roots = _roots(
                self.reads, surface, include_surface=not contextual or not self.reads,
            )
        self.root_ids = frozenset(_root_ids(self.roots))
        families = set(families_of(self.fold))
        for item in self.reads:
            families.update(families_of(item.lemma))
        self.families = families


@lru_cache(maxsize=512)
def _guess_pack(surface: str, *, include_pronouns: bool) -> _GuessPack:
    """Reuse the article independent analysis across state renders and guesses."""
    return _GuessPack(surface, include_pronouns=include_pronouns)


def _relation(guess: _Pack, target: _Pack) -> str:
    if not guess.fold or not target.fold:
        return ''
    if guess.fold == target.fold:
        if guess.service and target.service:
            return ''
        return 'exact'
    if target.contextual_reading == NEM_PRONOUN_CONTEXT:
        if (
            guess.lexemes & target.lexemes
            and any(item.lemma in {'он', 'оно'} for item in guess.reads)
        ):
            return 'lexeme'
        return ''
    numeric_root_match = _numeric_root_match(guess, target)
    if _alias(guess.fold, target.fold):
        return 'alias'
    guess_lemmas = {item.lemma for item in guess.reads}
    target_lemmas = {item.lemma for item in target.reads}
    for pair in _REVIEWED_LEXICAL_LINKS:
        guess_pair = pair & guess_lemmas
        target_pair = pair & target_lemmas
        if guess_pair and target_pair and not guess_pair & target_pair:
            return 'reviewed_lexical'
    if (guess.fold.isdigit() or target.fold.isdigit()) and not numeric_root_match:
        return ''
    # Import lazily: semantics imports the root bank, whose proper-name
    # installation can otherwise create an import-time cycle through match.
    from games.puzzles.censorly.lexical.semantics import (
        _PRONOUN_ADJECTIVE_LEMMAS,
        _proper_pair_from_readings,
    )
    if (guess.service or target.service) and not numeric_root_match:
        # Initial pronoun/number visibility must not erase an existing
        # lexeme relation (это/эти) or the reviewed cardinal/ordinal grammar
        # relation (два/второй). Keep one/первый split and service words
        # blocked as before.
        if guess.lexemes & target.lexemes:
            return 'lexeme'
        if (
            guess.roots & target.roots
            and any(
                item.pos == 'NPRO'
                or item.lemma in _PRONOUN_ADJECTIVE_LEMMAS
                for item in (*guess.reads, *target.reads)
            )
        ):
            return 'root'
        # «нем» is initially visible as a pronoun, but in label position it
        # also means «немецкий». Keep its reviewed German root usable as a
        # guess so the abbreviation can reveal Германия/немецкий.
        if (
            guess.fold == 'нем'
            and 'немецкий' in label_lemmas(guess.surface)
            and guess.roots & target.roots
        ):
            return 'root'
        if (
            _grammar(guess.reads, target.reads)
            and any(
                item.lemma in {
                    'два', 'три', 'четыре', 'пять', 'шесть',
                    'семь', 'восемь', 'девять',
                }
                for item in (*guess.reads, *target.reads)
            )
        ):
            return 'grammar'
        return ''
    if _proper_pair_from_readings(
        guess.fold,
        target.fold,
        guess.reads,
        target.reads,
        left_label=not guess.contextual_reading.startswith('__censorly_'),
        right_label=not target.contextual_reading.startswith('__censorly_'),
    ):
        return 'proper'
    if not guess.russian or not target.russian:
        return ''
    # The guess pack may prefer citation-form readings for root matching, but
    # that preference must not break the reverse direction of an inflectional
    # lexeme such as «улья» / «улей».
    if guess.lexemes & target.lexemes:
        return 'lexeme'
    if isinstance(guess, _GuessPack):
        reverse_lexemes = guess.lexemes_by_fold.get(target.fold, ())
    else:
        # Keep the helper's behavior for generic packs used by audits/tests;
        # gameplay guesses use _GuessPack and take the precomputed lookup.
        reverse_lexemes = {
            (item.para_id, item.lemma)
            for item in readings_of(guess.surface)
            if fold(item.lemma) == target.fold
        }
    if reverse_lexemes and reverse_lexemes & target.lexemes:
        return 'lexeme'
    if guess.roots & target.roots:
        return 'root'
    if _root_sense_compatible(guess.roots, target.roots):
        return 'root_sense_compatible'
    if _grammar(guess.reads, target.reads):
        return 'grammar'
    return ''


def _opens(guess: _Pack, target: _Pack) -> bool:
    return bool(_relation(guess, target))


def _root_ids(structures) -> set[str]:
    return {
        part for item in structures for part in item
        if not part.startswith('sense:')
    }


def _sense_ids(structures) -> set[str]:
    return {
        part for item in structures for part in item
        if part.startswith('sense:')
    }


def _reject(guess: _Pack, target: _Pack) -> str:
    if guess.service or target.service or not guess.russian or not target.russian:
        return ''
    if guess.root_ids & target.root_ids:
        return 'root_overlap'
    if guess.families & target.families:
        return 'family_split'
    return ''


class _ArticleIndex:
    """Inverted lexical postings for distinct surface and reading pairs."""

    def __init__(
        self, entries: tuple[tuple[str, str], ...], *, include_pronouns: bool,
    ):
        self.entries = entries
        self.surfaces = tuple(surface for surface, _lemma in entries)
        self.lemmas = tuple(lemma for _surface, lemma in entries)
        self.packs = tuple(
            _Pack(
                surface,
                include_pronouns=include_pronouns,
                article_lemma=lemma or None,
            )
            for surface, lemma in entries
        )
        closed_contexts = {
            CITY_ABBR_CONTEXT, NEM_GERMAN_CONTEXT, NEM_NONPRONOUN_CONTEXT,
            N_ERA_CONTEXT,
            PAGE_ABBR_CONTEXT, PO_SOFTWARE_CONTEXT, STREET_ABBR_CONTEXT,
            YEAR_ABBR_CONTEXT, *LANGUAGE_LABEL_CONTEXTS.values(),
        }
        for pack, lemma in zip(self.packs, self.lemmas):
            if lemma in closed_contexts:
                pack.service = False
        # Postings store compact integer surface IDs instead of repeating
        # references to strings in many Python sets.
        self.fold = defaultdict(list)
        self.alias = defaultdict(list)
        self.proper = defaultdict(list)
        self.lexeme = defaultdict(list)
        self.root = defaultdict(list)
        self.grammar = defaultdict(list)
        self.root_id = {}
        self.family = {}
        self._rejection_index_ready = False
        self._rejection_index_lock = Lock()
        for surface_id, (surface, pack) in enumerate(zip(self.surfaces, self.packs)):
            self.fold[pack.fold].append(surface_id)
            for group_id in _ALIAS_GROUPS_BY_WORD.get(pack.fold, ()):
                self.alias[group_id].append(surface_id)
            # _proper_pair uses readings_of directly, including the special
            # homographic readings omitted from gameplay's pack when needed.
            proper_reads = readings_of(surface)
            forms = {pack.fold} | {fold(item.lemma) for item in proper_reads if item.lemma}
            # Contextual language labels («кит.» → китайский) and reviewed
            # label stubs («укр») reach their country through these lemmas.
            proper_forms = forms | set(label_lemmas(surface)) | {
                fold(item.lemma) for item in pack.reads if item.lemma
            }
            for form in proper_forms:
                component = _PROPER_COMPONENT_BY_WORD.get(form)
                if component:
                    self.proper[component].append(surface_id)
            for form in forms:
                for group in _GRAMMAR_GROUPS_BY_WORD.get(form, ()):
                    self.grammar[group].append(surface_id)
            for key in pack.lexemes:
                self.lexeme[key].append(surface_id)
            for structure in pack.roots:
                self.root[structure].append(surface_id)
        # Python lists are convenient while building postings, but their
        # pointer storage dominates for high-frequency keys. Keep small
        # postings as lists and pack only the long ones into 32-bit IDs.
        for posting_map in (
            self.fold, self.alias, self.proper, self.lexeme,
            self.root, self.grammar,
        ):
            for key, posting in posting_map.items():
                if len(posting) >= 64:
                    posting_map[key] = array('I', posting)

    def _ensure_rejection_postings(self) -> None:
        """Build near-miss indexes only when a report needs them."""
        if self._rejection_index_ready:
            return
        with self._rejection_index_lock:
            if self._rejection_index_ready:
                return
            root_id = defaultdict(list)
            family = defaultdict(list)
            for surface_id, pack in enumerate(self.packs):
                for key in pack.root_ids:
                    root_id[key].append(surface_id)
                for key in pack.families:
                    family[key].append(surface_id)
            for posting_map in (root_id, family):
                for key, posting in posting_map.items():
                    if len(posting) >= 64:
                        posting_map[key] = array('I', posting)
            self.root_id = root_id
            self.family = family
            self._rejection_index_ready = True

    def candidate_ids(
        self, guess: _Pack, *, include_rejections: bool = True,
    ) -> list[int]:
        """Return IDs that can open, plus optional near-miss candidates."""
        postings = [self.fold.get(guess.fold, ())]
        from games.puzzles.censorly.lexical.semantics import (
            _INITIAL_NUMBER_LEMMAS,
            _PRONOUN_ADJECTIVE_LEMMAS,
        )
        initial_semantic = any(
            item.pos == 'NPRO'
            or item.lemma in _INITIAL_NUMBER_LEMMAS
            or item.lemma in _PRONOUN_ADJECTIVE_LEMMAS
            for item in guess.reads
        )
        if guess.service and not initial_semantic:
            return self._merge_postings(postings)
        for alias_group in _ALIAS_GROUPS_BY_WORD.get(guess.fold, ()):
            postings.append(self.alias.get(alias_group, ()))
        proper_reads = readings_of(guess.surface)
        for form in (
            {guess.fold, *label_lemmas(guess.surface)}
            | {fold(item.lemma) for item in proper_reads if item.lemma}
        ):
            component = _PROPER_COMPONENT_BY_WORD.get(form)
            if component:
                postings.append(self.proper.get(component, ()))
        if guess.russian:
            for key in guess.lexemes:
                postings.append(self.lexeme.get(key, ()))
            for structure in guess.roots:
                postings.append(self.root.get(structure, ()))
            for item in guess.reads:
                for group in _GRAMMAR_GROUPS_BY_WORD.get(item.lemma, ()):
                    postings.append(self.grammar.get(group, ()))
            if include_rejections:
                self._ensure_rejection_postings()
                for root_id in guess.root_ids:
                    postings.append(self.root_id.get(root_id, ()))
                for family in guess.families:
                    postings.append(self.family.get(family, ()))
        return self._merge_postings(postings)

    def _merge_postings(self, postings) -> list[int]:
        candidates = []
        posting_count = sum(len(posting) for posting in postings)
        # Sparse guesses usually touch only a few IDs. A set keeps that work
        # proportional to the postings instead of clearing a bitmap as large
        # as the entire article for every guess. Dense queries use the bitmap,
        # which is much smaller than a Python set at that scale.
        if posting_count <= max(1, len(self.surfaces) // 8):
            seen = set()
            for posting in postings:
                for surface_id in posting:
                    if surface_id not in seen:
                        seen.add(surface_id)
                        candidates.append(surface_id)
            return candidates

        seen = bytearray(len(self.surfaces))
        for posting in postings:
            for surface_id in posting:
                if not seen[surface_id]:
                    seen[surface_id] = 1
                    candidates.append(surface_id)
        return candidates

@lru_cache(maxsize=4)
def _index(
    entries: tuple[tuple[str, str], ...], *, include_pronouns: bool,
) -> _ArticleIndex:
    return _ArticleIndex(
        entries,
        include_pronouns=include_pronouns,
    )


def _unique_entries(rows: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(dict.fromkeys(rows))


def _build_article_analysis(
    tokens, *, include_pronouns: bool, cache_index: bool = True,
    include_report: bool = True,
) -> dict:
    rows = tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )
    lemmas_by_surface = _lemmas_by_surface(rows)
    counts_by_entry = None
    if include_report:
        counts_by_entry = defaultdict(list)
        for order, ((surface, lemma), occurrences) in enumerate(Counter(rows).items()):
            counts_by_entry[(surface, lemma)].append((order, lemma, occurrences))
    index = None
    if rows:
        entries = _unique_entries(rows)
        cache_index = (
            cache_index
            and len(entries) <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_SURFACES
            and len(rows) <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_TOKENS
        )
        index = (
            _index(
                entries,
                include_pronouns=include_pronouns,
            )
            if cache_index else
            _ArticleIndex(
                entries,
                include_pronouns=include_pronouns,
            )
        )
    return {
        'rows': rows,
        'counts_by_entry': counts_by_entry,
        'lemmas_by_surface': lemmas_by_surface,
        'index': index,
        'surface_count': len(_unique_entries(rows)),
        'token_count': len(rows),
    }


def _ensure_report_counts(analysis: dict) -> dict[tuple[str, str], list[tuple[int, str, int]]]:
    with _article_analysis_cache_lock:
        counts_by_entry = analysis.get('counts_by_entry')
        if counts_by_entry is None:
            counts_by_entry = defaultdict(list)
            for order, ((surface, lemma), occurrences) in enumerate(
                Counter(analysis['rows']).items()
            ):
                counts_by_entry[(surface, lemma)].append((order, lemma, occurrences))
            analysis['counts_by_entry'] = counts_by_entry
        return counts_by_entry


def _lemmas_by_surface(rows: tuple[tuple[str, str], ...]) -> dict[str, set[str]]:
    lemmas = defaultdict(set)
    for surface, lemma in rows:
        lemmas[surface].add(lemma)
    return lemmas


def _cached_article_analysis(cache_key, tokens, *, include_pronouns: bool) -> dict:
    key = (cache_key, include_pronouns)
    with _article_analysis_cache_lock:
        analysis = _article_analysis_cache.get(key)
        if analysis is not None:
            _article_analysis_cache.move_to_end(key)
            return analysis
        future = _article_analysis_inflight.get(key)
        if future is None:
            future = Future()
            _article_analysis_inflight[key] = future
            is_builder = True
        else:
            is_builder = False
    if not is_builder:
        return future.result()

    global _article_analysis_cache_surfaces, _article_analysis_cache_tokens
    try:
        # Only one worker builds an article index at a time. Other article
        # keys remain independent, and oversized analyses can still be shared
        # with requests already waiting for this same cold key.
        built = _build_article_analysis(
            tokens, include_pronouns=include_pronouns, cache_index=False,
            include_report=False,
        )
        if (
            built['surface_count'] <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_SURFACES
            and built['token_count'] <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_TOKENS
        ):
            with _article_analysis_cache_lock:
                _article_analysis_cache[key] = built
                _article_analysis_cache_surfaces += built['surface_count']
                _article_analysis_cache_tokens += built['token_count']
                while (
                    len(_article_analysis_cache) > _ARTICLE_ANALYSIS_CACHE_MAXSIZE
                    or _article_analysis_cache_surfaces > _ARTICLE_ANALYSIS_CACHE_MAX_SURFACES
                    or _article_analysis_cache_tokens > _ARTICLE_ANALYSIS_CACHE_MAX_TOKENS
                ):
                    _old_key, old_analysis = _article_analysis_cache.popitem(last=False)
                    _article_analysis_cache_surfaces -= old_analysis['surface_count']
                    _article_analysis_cache_tokens -= old_analysis['token_count']
        with _article_analysis_cache_lock:
            _article_analysis_inflight.pop(key, None)
            future.set_result(built)
        return built
    except BaseException as exc:
        with _article_analysis_cache_lock:
            _article_analysis_inflight.pop(key, None)
            future.set_exception(exc)
        raise


def matching_lemmas(tokens, guess: str, *, cache_key=None) -> set[str]:
    """Stored lemmas of content tokens the guess opens."""
    return analyze_guess(
        tokens, guess, cache_key=cache_key, include_report=False,
    )[0]


def analyze_guess(
    tokens, guess: str, *, cache_key=None, include_report: bool = True,
) -> tuple[set[str], dict]:
    """Resolve hits and the lexical review report from one candidate pass."""
    from games.puzzles.censorly.text.flags import open_pronouns_enabled
    include_pronouns = open_pronouns_enabled()
    if cache_key is None:
        analysis = _build_article_analysis(
            tokens, include_pronouns=include_pronouns,
            include_report=include_report,
        )
    else:
        analysis = _cached_article_analysis(
            cache_key, tokens, include_pronouns=include_pronouns,
        )
    rows = analysis['rows']
    empty = {'readings': [], 'opened': [], 'rejected': [], 'rejected_total': 0}
    if not rows or not (guess or '').strip():
        return set(), empty
    lemmas_by_surface = analysis['lemmas_by_surface']
    index = analysis['index']
    guess_fold = fold(guess)
    article_lemmas = {
        lemma
        for surface_id in index.fold.get(guess_fold, ())
        for lemma in (index.lemmas[surface_id],)
    }
    if len(article_lemmas) == 1:
        guess_pack = _GuessPack(
            guess,
            include_pronouns=include_pronouns,
            article_lemma=next(iter(article_lemmas)),
        )
    else:
        guess_pack = _guess_pack(guess, include_pronouns=include_pronouns)
    candidate_ids = index.candidate_ids(
        guess_pack, include_rejections=include_report,
    )
    if not include_report:
        hits = set()
        for surface_id in candidate_ids:
            pack = index.packs[surface_id]
            if _relation(guess_pack, pack):
                lemma = index.lemmas[surface_id]
                hits.add(lemma or pack.fold or guess_pack.fold)
        return hits, empty

    counts_by_entry = _ensure_report_counts(analysis)

    opened: dict[tuple, dict] = {}
    rejected: dict[tuple, dict] = {}
    relations_by_id = {}
    rejections_by_id = {}
    hits: set[str] = set()
    for surface_id in candidate_ids:
        pack = index.packs[surface_id]
        relation = _relation(guess_pack, pack)
        relations_by_id[surface_id] = relation
        if not relation:
            rejections_by_id[surface_id] = _reject(guess_pack, pack)

    for surface_id in candidate_ids:
        if relations_by_id[surface_id]:
            pack = index.packs[surface_id]
            lemma = index.lemmas[surface_id]
            hits.add(lemma or pack.fold or guess_pack.fold)

    # Merge only candidate rows, then restore the previous first-seen report
    # order. This avoids walking every distinct article row on every guess.
    report_rows = sorted(
        (order, surface_id, lemma, occurrences)
        for surface_id in candidate_ids
        for order, lemma, occurrences in counts_by_entry[(
            index.surfaces[surface_id], index.lemmas[surface_id],
        )]
    )
    for _order, surface_id, lemma, occurrences in report_rows:
        surface = index.surfaces[surface_id]
        pack = index.packs[surface_id]
        stored = lemma or pack.fold
        reason = relations_by_id.get(surface_id, '')
        bucket = opened
        if not reason:
            reason = rejections_by_id.get(surface_id, '')
            bucket = rejected
        if not reason:
            continue
        key = (pack.fold, stored, reason)
        slot = bucket.get(key)
        if slot is None:
            slot = {'surface': surface, 'lemma': stored, 'reason': reason, 'count': 0}
            bucket[key] = slot
        slot['count'] += occurrences
    rejected_rows = list(rejected.values())
    report = {
        'readings': [
            {'lemma': item.lemma, 'pos': item.pos, 'score': round(item.score, 3)}
            for item in guess_pack.reads
        ],
        'opened': list(opened.values()),
        'rejected': rejected_rows[:_REJECT_CAP],
        'rejected_total': len(rejected_rows),
    }
    return hits, report


def decision(tokens, guess: str) -> dict:
    """Opened targets and near-miss candidates for one guess."""
    return analyze_guess(tokens, guess)[1]
