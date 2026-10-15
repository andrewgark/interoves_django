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

from games.censorly.lexical.core import fold
from games.censorly.lexical.rootbank import families_of
from games.censorly.lexical.russian.reading_overrides import (
    N_ERA_CONTEXT,
    NEM_NONPRONOUN_CONTEXT,
    NEM_PRONOUN_CONTEXT,
    target_lemma_override,
    target_readings,
)
from games.censorly.lexical.semantics import (
    _alias,
    _ALIASES,
    _grammar,
    _GRAMMAR,
    _PROPER_COMPONENT_BY_WORD,
    _roots,
    _russian,
    guess_readings_of,
    initially_open,
    preferred_readings_of,
    readings_of,
)

_REJECT_CAP = 200


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


class _Pack:
    __slots__ = (
        'surface', 'fold', 'service', 'russian', 'lexemes', 'roots',
        'root_ids', 'reads', 'families',
    )

    def __init__(
        self, surface: str, *, include_pronouns: bool = True,
        article_lemma: str | None = None,
    ):
        self.surface = surface
        self.fold = fold(surface)
        self.service = initially_open(surface, include_pronouns=include_pronouns)
        self.reads = preferred_readings_of(surface)
        # OpenCorpora/pymorphy also tags the standalone letter name «Е» as a
        # rare present-tense form of «быть». Treat that token as the letter
        # sense installed in semantic_splits, so ordinary forms of «быть» do
        # not reveal it through the spurious homographic reading.
        letter_lemma = _LETTER_NAME_LEMMAS.get(self.fold)
        if letter_lemma:
            self.reads = tuple(item for item in self.reads if item.lemma == letter_lemma)
        contextual = False
        if article_lemma:
            override_lemmas = target_lemma_override(surface, article_lemma)
            if override_lemmas:
                self.reads = tuple(
                    item
                    for lemma in override_lemmas
                    for item in preferred_readings_of(lemma)
                )
                contextual = True
            else:
                self.reads, contextual = target_readings(
                    surface, article_lemma, self.reads,
                )
        self.russian = _russian(surface, 'ru')
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        # Root equality uses citation-preferred readings on both sides.  The
        # broader noun alternatives remain in ``reads`` for SAME_LEXEME.
        root_reads = guess_readings_of(surface)
        if article_lemma:
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
        self.roots = _roots(root_reads, surface, include_surface=not contextual)
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
        self.reads = guess_readings_of(surface)
        # Keep the standalone letter-name correction when rebuilding the
        # citation-form guess pack; otherwise article scans can resurrect the
        # spurious «Е» -> «быть» reading filtered by _Pack.
        letter_lemma = _LETTER_NAME_LEMMAS.get(self.fold)
        if letter_lemma:
            self.reads = tuple(item for item in self.reads if item.lemma == letter_lemma)
        contextual = False
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
        self.roots = _roots(self.reads, surface, include_surface=not contextual)
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
        return 'exact'
    if guess.service or target.service:
        return ''
    if _alias(guess.fold, target.fold):
        return 'alias'
    # Import lazily: semantics imports the root bank, whose proper-name
    # installation can otherwise create an import-time cycle through match.
    from games.censorly.lexical.semantics import _proper_pair_from_readings

    if _proper_pair_from_readings(
        guess.fold,
        target.fold,
        guess.reads,
        target.reads,
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
    if _grammar(guess.reads, target.reads):
        return 'grammar'
    return ''


def _opens(guess: _Pack, target: _Pack) -> bool:
    return bool(_relation(guess, target))


def _root_ids(structures) -> set[str]:
    return {part for item in structures for part in item}


def _reject(guess: _Pack, target: _Pack) -> str:
    if guess.service or target.service or not guess.russian or not target.russian:
        return ''
    if guess.root_ids & target.root_ids:
        return 'root_overlap'
    if guess.families & target.families:
        return 'family_split'
    return ''


class _ArticleIndex:
    """Inverted lexical postings for one article's distinct token surfaces."""

    def __init__(
        self, surfaces: tuple[str, ...], *, include_pronouns: bool,
        target_contexts: tuple[tuple[str, str], ...] = (),
    ):
        self.surfaces = surfaces
        context_by_surface = dict(target_contexts)
        self.packs = tuple(
            _Pack(
                surface,
                include_pronouns=include_pronouns,
                article_lemma=context_by_surface.get(surface),
            )
            for surface in surfaces
        )
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
        for surface_id, (surface, pack) in enumerate(zip(surfaces, self.packs)):
            self.fold[pack.fold].append(surface_id)
            for group_id in _ALIAS_GROUPS_BY_WORD.get(pack.fold, ()):
                self.alias[group_id].append(surface_id)
            # _proper_pair uses readings_of directly, including the special
            # homographic readings omitted from gameplay's pack when needed.
            proper_reads = readings_of(surface)
            forms = {pack.fold} | {fold(item.lemma) for item in proper_reads if item.lemma}
            for form in forms:
                component = _PROPER_COMPONENT_BY_WORD.get(form)
                if component:
                    self.proper[component].append(surface_id)
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
        if guess.service:
            return self._merge_postings(postings)
        for alias_group in _ALIAS_GROUPS_BY_WORD.get(guess.fold, ()):
            postings.append(self.alias.get(alias_group, ()))
        proper_reads = readings_of(guess.surface)
        for form in {guess.fold} | {fold(item.lemma) for item in proper_reads if item.lemma}:
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

    def candidates(self, guess: _Pack) -> set[str]:
        """Compatibility wrapper for tools that consume surface strings."""
        return {self.surfaces[surface_id] for surface_id in self.candidate_ids(guess)}


@lru_cache(maxsize=4)
def _index(
    surfaces: tuple[str, ...], *, include_pronouns: bool,
    target_contexts: tuple[tuple[str, str], ...] = (),
) -> _ArticleIndex:
    return _ArticleIndex(
        surfaces,
        include_pronouns=include_pronouns,
        target_contexts=target_contexts,
    )


def _unique_surfaces(rows: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(surface for surface, _lemma in rows))


def _build_article_analysis(
    tokens, *, include_pronouns: bool, cache_index: bool = True,
    include_report: bool = True,
) -> dict:
    rows = tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )
    surfaces = _unique_surfaces(rows)
    lemmas_by_surface = _lemmas_by_surface(rows)
    target_contexts = tuple(sorted(_target_contexts(tokens, rows).items()))
    counts_by_surface = None
    if include_report:
        counts_by_surface = defaultdict(list)
        for order, ((surface, lemma), occurrences) in enumerate(Counter(rows).items()):
            counts_by_surface[surface].append((order, lemma, occurrences))
    index = None
    if rows:
        cache_index = (
            cache_index
            and len(surfaces) <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_SURFACES
            and len(rows) <= _ARTICLE_ANALYSIS_CACHE_MAX_SINGLE_TOKENS
        )
        index = (
            _index(
                surfaces,
                include_pronouns=include_pronouns,
                target_contexts=target_contexts,
            )
            if cache_index else
            _ArticleIndex(
                surfaces,
                include_pronouns=include_pronouns,
                target_contexts=target_contexts,
            )
        )
    return {
        'rows': rows,
        'counts_by_surface': counts_by_surface,
        'lemmas_by_surface': lemmas_by_surface,
        'index': index,
        'surface_count': len(surfaces),
        'token_count': len(rows),
    }


def _ensure_report_counts(analysis: dict) -> dict[str, list[tuple[int, str, int]]]:
    with _article_analysis_cache_lock:
        counts_by_surface = analysis.get('counts_by_surface')
        if counts_by_surface is None:
            counts_by_surface = defaultdict(list)
            for order, ((surface, lemma), occurrences) in enumerate(
                Counter(analysis['rows']).items()
            ):
                counts_by_surface[surface].append((order, lemma, occurrences))
            analysis['counts_by_surface'] = counts_by_surface
        return counts_by_surface


def _lemmas_by_surface(rows: tuple[tuple[str, str], ...]) -> dict[str, set[str]]:
    lemmas = defaultdict(set)
    for surface, lemma in rows:
        lemmas[surface].add(lemma)
    return lemmas


def _target_contexts(tokens, rows):
    """Return ordinary lemma contexts plus the narrow «нем» context marker."""
    lemmas = _lemmas_by_surface(rows)
    contexts = {
        surface: next(iter(surface_lemmas))
        for surface, surface_lemmas in lemmas.items()
        if len(surface_lemmas) == 1
    }
    n_positions = [
        index for index, token in enumerate(tokens)
        if token.get('kind') == 'content' and fold(token.get('surface') or '') == 'н'
    ]
    n_era_positions = {
        index for index in n_positions
        if _is_n_era_abbreviation(tokens, index)
    }
    # A surface-level pack cannot represent occurrence-specific readings. If
    # an article contains both «н. э.» and an unrelated standalone «н», keep
    # the ordinary readings for all of them rather than over-expanding «н».
    if n_positions and len(n_era_positions) == len(n_positions):
        for index in n_era_positions:
            surface = tokens[index].get('surface') or ''
            contexts[surface] = N_ERA_CONTEXT

    nem_positions = [
        index for index, token in enumerate(tokens)
        if token.get('kind') == 'content' and fold(token.get('surface') or '') == 'нем'
    ]
    if nem_positions:
        prepositions = {
            'в', 'во', 'к', 'ко', 'на', 'над', 'о', 'об', 'обо', 'от', 'при',
            'с', 'со', 'у', 'перед', 'про', 'через',
        }
        nem_contexts = set()
        for index in nem_positions:
            previous = index - 1
            while previous >= 0 and tokens[previous].get('kind') == 'space':
                previous -= 1
            next_index = index + 1
            while next_index < len(tokens) and tokens[next_index].get('kind') == 'space':
                next_index += 1
            previous_surface = fold(tokens[previous].get('surface') or '') if previous >= 0 else ''
            next_surface = tokens[next_index].get('surface') if next_index < len(tokens) else ''
            if previous_surface in prepositions:
                nem_contexts.add(NEM_PRONOUN_CONTEXT)
            else:
                nem_contexts.add(NEM_NONPRONOUN_CONTEXT)
            if next_surface == '.':
                nem_contexts.add(NEM_NONPRONOUN_CONTEXT)
        if len(nem_contexts) == 1:
            contexts['нем'] = next(iter(nem_contexts))
        else:
            # One surface pack cannot represent two occurrence-specific readings.
            # Keep the ordinary tokenizer lemma rather than inventing a global
            # pronoun/non-pronoun decision for the whole article.
            contexts.pop('нем', None)
    return contexts


def _is_n_era_abbreviation(tokens, index: int) -> bool:
    """Whether content token ``index`` is the «н.» in «н. э.» exactly."""
    if index < 0 or index >= len(tokens):
        return False
    token = tokens[index]
    if token.get('kind') != 'content' or fold(token.get('surface') or '') != 'н':
        return False

    def next_nonspace(position):
        while position < len(tokens) and tokens[position].get('kind') == 'space':
            position += 1
        return position

    dot_after_n = next_nonspace(index + 1)
    if (
        dot_after_n >= len(tokens)
        or tokens[dot_after_n].get('kind') != 'punct'
        or tokens[dot_after_n].get('surface') != '.'
    ):
        return False
    e_position = next_nonspace(dot_after_n + 1)
    if (
        e_position >= len(tokens)
        or tokens[e_position].get('kind') != 'content'
        or fold(tokens[e_position].get('surface') or '') != 'э'
    ):
        return False
    dot_after_e = next_nonspace(e_position + 1)
    return (
        dot_after_e < len(tokens)
        and tokens[dot_after_e].get('kind') == 'punct'
        and tokens[dot_after_e].get('surface') == '.'
    )


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
    from games.censorly.flags import open_pronouns_enabled
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
        for lemma in lemmas_by_surface[index.surfaces[surface_id]]
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
            surface = index.surfaces[surface_id]
            pack = index.packs[surface_id]
            if _relation(guess_pack, pack):
                hits.update(
                    lemma or pack.fold or guess_pack.fold
                    for lemma in lemmas_by_surface[surface]
                )
        return hits, empty

    counts_by_surface = _ensure_report_counts(analysis)

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
            surface = index.surfaces[surface_id]
            pack = index.packs[surface_id]
            hits.update(
                lemma or pack.fold or guess_pack.fold
                for lemma in lemmas_by_surface[surface]
            )

    # Merge only candidate rows, then restore the previous first-seen report
    # order. This avoids walking every distinct article row on every guess.
    report_rows = sorted(
        (order, surface_id, lemma, occurrences)
        for surface_id in candidate_ids
        for order, lemma, occurrences in counts_by_surface[index.surfaces[surface_id]]
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
