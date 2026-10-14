"""One-pass article index for the gameplay resolver.

A guess is analyzed once. Each target token is analyzed once per article
and then reused. Comparison is the same existential check as ``explain``.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import Future
from functools import lru_cache
from threading import RLock

from games.censorly.lexical.core import fold
from games.censorly.lexical.rootbank import families_of
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


class _Pack:
    __slots__ = ('surface', 'fold', 'service', 'russian', 'lexemes', 'roots', 'reads', 'families')

    def __init__(self, surface: str, *, include_pronouns: bool = True):
        self.surface = surface
        self.fold = fold(surface)
        self.service = initially_open(surface, include_pronouns=include_pronouns)
        self.reads = readings_of(surface)
        # OpenCorpora/pymorphy also tags the standalone letter name «Е» as a
        # rare present-tense form of «быть». Treat that token as the letter
        # sense installed in semantic_splits, so ordinary forms of «быть» do
        # not reveal it through the spurious homographic reading.
        if self.fold == 'е':
            self.reads = tuple(item for item in self.reads if item.lemma == 'е')
        self.russian = _russian(surface, 'ru')
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        self.roots = _roots(self.reads, surface)
        families = set(families_of(self.fold))
        for item in self.reads:
            families.update(families_of(item.lemma))
        self.families = families


class _GuessPack(_Pack):
    """Article-independent pack with citation-form preference."""

    def __init__(self, surface: str, *, include_pronouns: bool = True):
        super().__init__(surface, include_pronouns=include_pronouns)
        self.reads = guess_readings_of(surface)
        self.lexemes = {(item.para_id, item.lemma) for item in self.reads}
        self.roots = _roots(self.reads, surface)
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
    from games.censorly.lexical.semantics import _proper_pair

    if _proper_pair(guess.fold, target.fold):
        return 'proper'
    if not guess.russian or not target.russian:
        return ''
    if guess.lexemes & target.lexemes:
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
    if _root_ids(guess.roots) & _root_ids(target.roots):
        return 'root_overlap'
    if guess.families & target.families:
        return 'family_split'
    return ''


class _ArticleIndex:
    """Inverted lexical postings for one article's distinct token surfaces."""

    def __init__(self, surfaces: tuple[str, ...], *, include_pronouns: bool):
        self.surfaces = surfaces
        self.packs = tuple(_Pack(s, include_pronouns=include_pronouns) for s in surfaces)
        self.by_surface = dict(zip(surfaces, self.packs))
        self.fold = defaultdict(set)
        self.alias = defaultdict(set)
        self.proper = defaultdict(set)
        self.lexeme = defaultdict(set)
        self.root = defaultdict(set)
        self.grammar = defaultdict(set)
        self.root_id = defaultdict(set)
        self.family = defaultdict(set)
        for surface, pack in zip(surfaces, self.packs):
            self.fold[pack.fold].add(surface)
            for group_id in _ALIAS_GROUPS_BY_WORD.get(pack.fold, ()):
                self.alias[group_id].add(surface)
            # _proper_pair uses readings_of directly, including the special
            # homographic readings omitted from gameplay's pack when needed.
            proper_reads = readings_of(surface)
            forms = {pack.fold} | {fold(item.lemma) for item in proper_reads if item.lemma}
            for form in forms:
                component = _PROPER_COMPONENT_BY_WORD.get(form)
                if component:
                    self.proper[component].add(surface)
                for group in _GRAMMAR_GROUPS_BY_WORD.get(form, ()):
                    self.grammar[group].add(surface)
            for key in pack.lexemes:
                self.lexeme[key].add(surface)
            for structure in pack.roots:
                self.root[structure].add(surface)
                for root_id in structure:
                    self.root_id[root_id].add(surface)
            for family in pack.families:
                self.family[family].add(surface)

    def candidates(self, guess: _Pack) -> set[str]:
        """Return only surfaces that can open or be reported as near misses."""
        out = set(self.fold.get(guess.fold, ()))
        if guess.service:
            return out
        for alias_group in _ALIAS_GROUPS_BY_WORD.get(guess.fold, ()):
            out.update(self.alias.get(alias_group, ()))
        proper_reads = readings_of(guess.surface)
        for form in {guess.fold} | {fold(item.lemma) for item in proper_reads if item.lemma}:
            component = _PROPER_COMPONENT_BY_WORD.get(form)
            if component:
                out.update(self.proper.get(component, ()))
        if guess.russian:
            for key in guess.lexemes:
                out.update(self.lexeme.get(key, ()))
            for structure in guess.roots:
                out.update(self.root.get(structure, ()))
            for item in guess.reads:
                for group in _GRAMMAR_GROUPS_BY_WORD.get(item.lemma, ()):
                    out.update(self.grammar.get(group, ()))
            root_ids = _root_ids(guess.roots)
            for root_id in root_ids:
                out.update(self.root_id.get(root_id, ()))
            for family in guess.families:
                out.update(self.family.get(family, ()))
        return out


@lru_cache(maxsize=4)
def _index(surfaces: tuple[str, ...], *, include_pronouns: bool) -> _ArticleIndex:
    return _ArticleIndex(surfaces, include_pronouns=include_pronouns)


def _unique_surfaces(rows: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(surface for surface, _lemma in rows))


def _build_article_analysis(
    tokens, *, include_pronouns: bool, cache_index: bool = True,
) -> dict:
    rows = tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )
    surfaces = _unique_surfaces(rows)
    index = None
    if rows:
        index = (
            _index(surfaces, include_pronouns=include_pronouns)
            if cache_index else
            _ArticleIndex(surfaces, include_pronouns=include_pronouns)
        )
    return {
        'rows': rows,
        'counts': Counter(rows),
        'lemmas_by_surface': _lemmas_by_surface(rows),
        'index': index,
        'surface_count': len(surfaces),
        'token_count': len(rows),
    }


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


def matching_lemmas(tokens, guess: str) -> set[str]:
    """Stored lemmas of content tokens the guess opens."""
    return analyze_guess(tokens, guess)[0]


def analyze_guess(tokens, guess: str, *, cache_key=None) -> tuple[set[str], dict]:
    """Resolve hits and the lexical review report from one candidate pass."""
    from games.censorly.flags import open_pronouns_enabled
    include_pronouns = open_pronouns_enabled()
    if cache_key is None:
        analysis = _build_article_analysis(tokens, include_pronouns=include_pronouns)
    else:
        analysis = _cached_article_analysis(
            cache_key, tokens, include_pronouns=include_pronouns,
        )
    rows = analysis['rows']
    empty = {'readings': [], 'opened': [], 'rejected': [], 'rejected_total': 0}
    if not rows or not (guess or '').strip():
        return set(), empty
    guess_pack = _guess_pack(guess, include_pronouns=include_pronouns)
    lemmas_by_surface = analysis['lemmas_by_surface']
    index = analysis['index']
    candidate_surfaces = index.candidates(guess_pack)
    opened: dict[tuple, dict] = {}
    rejected: dict[tuple, dict] = {}
    packs_by_surface = {surface: index.by_surface[surface] for surface in candidate_surfaces}
    relations_by_surface = {}
    rejections_by_surface = {}
    hits: set[str] = set()
    for surface, pack in packs_by_surface.items():
        relation = _relation(guess_pack, pack)
        relations_by_surface[surface] = relation
        if not relation:
            rejections_by_surface[surface] = _reject(guess_pack, pack)

    for surface in candidate_surfaces:
        if relations_by_surface[surface]:
            pack = packs_by_surface[surface]
            hits.update(
                lemma or pack.fold or guess_pack.fold
                for lemma in lemmas_by_surface[surface]
            )

    # Counter preserves the first-seen order of row pairs. Aggregating equal
    # occurrences keeps the review report identical while avoiding repeated
    # lexical comparisons for every occurrence of a surface.
    for (surface, lemma), occurrences in analysis['counts'].items():
        pack = index.by_surface[surface]
        stored = lemma or pack.fold
        reason = relations_by_surface.get(surface, '')
        bucket = opened
        if not reason:
            reason = rejections_by_surface.get(surface, '')
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
