"""``ArticleIndex``: an article's tokens analyzed once, with layer postings.

A guess is analyzed once; the index returns the tokens that share a key of
some layer with it, and ``Matcher.relation`` decides each. Postings come
only from the layers' own ``target_keys``, so a layer cannot open a pair the
index never proposes.
"""

from __future__ import annotations

from array import array
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import Future
from functools import lru_cache
from threading import Lock, RLock

from games.puzzles.censorly.lexical.context import context_of
from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.deciders.analyzer import ANALYZER
from games.puzzles.censorly.lexical.deciders.layers import (
    ALIAS,
    EXACT,
    GRAMMAR,
    LEXEME,
    PROPER,
    ROOT,
)
from games.puzzles.censorly.lexical.deciders.matcher import MATCHER

_INDEXED_LAYERS = (EXACT, ALIAS, PROPER, LEXEME, ROOT, GRAMMAR)
_PACKED_POSTING = 64
_REJECT_CAP = 200
_CACHE_MAX_ARTICLES = 4
_CACHE_MAX_SURFACES = 12000
_CACHE_MAX_SINGLE_SURFACES = 6000
_CACHE_MAX_TOKENS = 40000
_CACHE_MAX_SINGLE_TOKENS = 20000


def _pack_postings(posting_map) -> None:
    """Long postings as 32-bit arrays; list pointers dominate memory there."""
    for key, posting in posting_map.items():
        if len(posting) >= _PACKED_POSTING:
            posting_map[key] = array('I', posting)


class ArticleIndex:
    """Inverted postings for distinct (surface, lemma) entries of one article."""

    def __init__(self, entries: tuple[tuple[str, str], ...], *, include_pronouns: bool):
        self.entries = entries
        self.surfaces = tuple(surface for surface, _lemma in entries)
        self.lemmas = tuple(lemma for _surface, lemma in entries)
        self.words = tuple(
            ANALYZER.target(surface, article_lemma=lemma, include_pronouns=include_pronouns)
            for surface, lemma in entries
        )
        self.postings = {layer: defaultdict(list) for layer in _INDEXED_LAYERS}
        for word_id, word in enumerate(self.words):
            for layer in _INDEXED_LAYERS:
                posting_map = self.postings[layer]
                for key in dict.fromkeys(layer.target_keys(word)):
                    posting_map[key].append(word_id)
        for posting_map in self.postings.values():
            _pack_postings(posting_map)
        self._near_miss: dict | None = None
        self._near_miss_lock = Lock()

    def _near_miss_postings(self) -> dict:
        """Root-id and family postings, built only when a report needs them."""
        if self._near_miss is not None:
            return self._near_miss
        with self._near_miss_lock:
            if self._near_miss is None:
                root_id, family = defaultdict(list), defaultdict(list)
                for word_id, word in enumerate(self.words):
                    for key in word.root_ids:
                        root_id[key].append(word_id)
                    for key in word.families:
                        family[key].append(word_id)
                _pack_postings(root_id)
                _pack_postings(family)
                self._near_miss = {'root_id': root_id, 'family': family}
        return self._near_miss

    def candidate_ids(self, guess, *, include_near_misses: bool = True) -> list[int]:
        postings = []
        layers = MATCHER.guess_layers(guess)
        for layer in layers:
            posting_map = self.postings[layer.postings_of or layer]
            for key in layer.guess_keys(guess):
                postings.append(posting_map.get(key, ()))
        if include_near_misses and guess.russian and len(layers) > 1:
            near = self._near_miss_postings()
            for key in guess.root_ids:
                postings.append(near['root_id'].get(key, ()))
            for key in guess.families:
                postings.append(near['family'].get(key, ()))
        return self._merge(postings)

    def _merge(self, postings) -> list[int]:
        candidates = []
        # Sparse guesses touch few IDs: a set is proportional to the
        # postings. Dense ones use a bitmap the size of the article.
        if sum(len(posting) for posting in postings) <= max(1, len(self.surfaces) // 8):
            seen = set()
            for posting in postings:
                for word_id in posting:
                    if word_id not in seen:
                        seen.add(word_id)
                        candidates.append(word_id)
            return candidates
        seen_map = bytearray(len(self.surfaces))
        for posting in postings:
            for word_id in posting:
                if not seen_map[word_id]:
                    seen_map[word_id] = 1
                    candidates.append(word_id)
        return candidates


# ── article analysis cache ─────────────────────────────────────────────────

_cache: OrderedDict = OrderedDict()
_cache_surfaces = 0
_cache_tokens = 0
_cache_lock = RLock()
_inflight: dict[tuple, Future] = {}


@lru_cache(maxsize=_CACHE_MAX_ARTICLES)
def _index(entries: tuple[tuple[str, str], ...], *, include_pronouns: bool) -> ArticleIndex:
    return ArticleIndex(entries, include_pronouns=include_pronouns)


def _content_rows(tokens) -> tuple[tuple[str, str], ...]:
    return tuple(
        ((tok.get('surface') or ''), (tok.get('lemma') or ''))
        for tok in tokens
        if tok.get('kind') == 'content'
    )


def _report_counts(rows) -> dict:
    counts = defaultdict(list)
    for order, ((surface, lemma), occurrences) in enumerate(Counter(rows).items()):
        counts[(surface, lemma)].append((order, lemma, occurrences))
    return counts


def build_article_analysis(
    tokens, *, include_pronouns: bool, cache_index: bool = True, include_report: bool = True,
) -> dict:
    rows = _content_rows(tokens)
    entries = tuple(dict.fromkeys(rows))
    index = None
    if rows:
        cache_index = (
            cache_index
            and len(entries) <= _CACHE_MAX_SINGLE_SURFACES
            and len(rows) <= _CACHE_MAX_SINGLE_TOKENS
        )
        index = (
            _index(entries, include_pronouns=include_pronouns)
            if cache_index else ArticleIndex(entries, include_pronouns=include_pronouns)
        )
    return {
        'rows': rows,
        'counts_by_entry': _report_counts(rows) if include_report else None,
        'index': index,
        'surface_count': len(entries),
        'token_count': len(rows),
    }


def _ensure_report_counts(analysis: dict) -> dict:
    with _cache_lock:
        if analysis.get('counts_by_entry') is None:
            analysis['counts_by_entry'] = _report_counts(analysis['rows'])
        return analysis['counts_by_entry']


def cached_article_analysis(cache_key, tokens, *, include_pronouns: bool) -> dict:
    """One analysis per article key and process; concurrent builds of one key share it."""
    global _cache_surfaces, _cache_tokens
    key = (cache_key, include_pronouns)
    with _cache_lock:
        analysis = _cache.get(key)
        if analysis is not None:
            _cache.move_to_end(key)
            return analysis
        future = _inflight.get(key)
        is_builder = future is None
        if is_builder:
            future = Future()
            _inflight[key] = future
    if not is_builder:
        return future.result()
    try:
        built = build_article_analysis(
            tokens, include_pronouns=include_pronouns, cache_index=False, include_report=False,
        )
        if (
            built['surface_count'] <= _CACHE_MAX_SINGLE_SURFACES
            and built['token_count'] <= _CACHE_MAX_SINGLE_TOKENS
        ):
            with _cache_lock:
                _cache[key] = built
                _cache_surfaces += built['surface_count']
                _cache_tokens += built['token_count']
                while (
                    len(_cache) > _CACHE_MAX_ARTICLES
                    or _cache_surfaces > _CACHE_MAX_SURFACES
                    or _cache_tokens > _CACHE_MAX_TOKENS
                ):
                    _old_key, old = _cache.popitem(last=False)
                    _cache_surfaces -= old['surface_count']
                    _cache_tokens -= old['token_count']
        with _cache_lock:
            _inflight.pop(key, None)
            future.set_result(built)
        return built
    except BaseException as exc:
        with _cache_lock:
            _inflight.pop(key, None)
            future.set_exception(exc)
        raise


def _open_pronouns() -> bool:
    from games.puzzles.censorly.text.flags import open_pronouns_enabled

    return open_pronouns_enabled()


def analyze_guess(tokens, guess: str, *, cache_key=None, include_report: bool = True):
    """Stored lemmas the guess opens, and the review report (opened, near misses)."""
    include_pronouns = _open_pronouns()
    if cache_key is None:
        analysis = build_article_analysis(
            tokens, include_pronouns=include_pronouns, include_report=include_report,
        )
    else:
        analysis = cached_article_analysis(cache_key, tokens, include_pronouns=include_pronouns)
    empty = {'readings': [], 'opened': [], 'rejected': [], 'rejected_total': 0}
    if not analysis['rows'] or not (guess or '').strip():
        return set(), empty
    index = analysis['index']
    guess_word, *also = guess_words(index, guess, include_pronouns=include_pronouns)
    candidate_ids = index.candidate_ids(guess_word, include_near_misses=include_report)
    seen = set(candidate_ids)
    for extra in also:
        for word_id in index.candidate_ids(extra, include_near_misses=False):
            if word_id not in seen:
                seen.add(word_id)
                candidate_ids.append(word_id)
    hits: set[str] = set()
    relations = {}
    for word_id in candidate_ids:
        word = index.words[word_id]
        relation = ''
        for reading in (guess_word, *also):
            relation = MATCHER.relation(reading, word)
            if relation:
                break
        relations[word_id] = relation
        if relation:
            hits.add(index.lemmas[word_id] or word.fold or guess_word.fold)
    if not include_report:
        return hits, empty

    counts_by_entry = _ensure_report_counts(analysis)
    opened: dict[tuple, dict] = {}
    rejected: dict[tuple, dict] = {}
    report_rows = sorted(
        (order, word_id, lemma, occurrences)
        for word_id in candidate_ids
        for order, lemma, occurrences in counts_by_entry[(index.surfaces[word_id], index.lemmas[word_id])]
    )
    for _order, word_id, lemma, occurrences in report_rows:
        word = index.words[word_id]
        reason = relations[word_id]
        bucket = opened
        if not reason:
            reason = MATCHER.near_miss(guess_word, word)
            bucket = rejected
        if not reason:
            continue
        stored = lemma or word.fold
        slot = bucket.setdefault((word.fold, stored, reason), {
            'surface': index.surfaces[word_id], 'lemma': stored, 'reason': reason, 'count': 0,
        })
        slot['count'] += occurrences
    rejected_rows = list(rejected.values())
    return hits, {
        'readings': [
            {'lemma': item.lemma, 'pos': item.pos, 'score': round(item.score, 3)}
            for item in guess_word.reads
        ],
        'opened': list(opened.values()),
        'rejected': rejected_rows[:_REJECT_CAP],
        'rejected_total': len(rejected_rows),
    }


def guess_words(index: ArticleIndex, guess: str, *, include_pronouns: bool) -> list:
    """How this article reads a typed guess; the first word is the main one.

    The article settles a guess spelled like its own tokens:

    * one lemma for that spelling → the guess takes it («Граната» in a garnet
      article is the garnet);
    * a token read as an expanding context (``порт.`` before a gloss) → the
      guess also reads as the context's lemma (португальский).
    """
    article_lemmas = {index.lemmas[word_id] for word_id in index.postings[EXACT].get(fold(guess), ())}
    words = [ANALYZER.guess(
        guess,
        article_lemma=next(iter(article_lemmas)) if len(article_lemmas) == 1 else '',
        include_pronouns=include_pronouns,
    )]
    for lemma in sorted(article_lemmas):
        context = context_of(lemma)
        if context is not None and context.expands_guess:
            words.extend(
                ANALYZER.guess(full, include_pronouns=include_pronouns) for full in context.lemmas
            )
    return words


def matching_lemmas(tokens, guess: str, *, cache_key=None) -> set[str]:
    """Stored lemmas of content tokens the guess opens."""
    return analyze_guess(tokens, guess, cache_key=cache_key, include_report=False)[0]


def decision(tokens, guess: str) -> dict:
    """Opened targets and near-miss candidates for one guess."""
    return analyze_guess(tokens, guess)[1]


def same_word(guess: str, target: str, target_lemma: str = '') -> bool:
    """Whether the matcher sees ``target`` as a form of ``guess`` (exact or lexeme).

    Unlike comparing stored lemmas, this survives pymorphy picking different
    top readings for forms of one lexeme («Граната» → гранат, «гранату» → граната).
    """
    include_pronouns = _open_pronouns()
    relation = MATCHER.relation(
        ANALYZER.guess(guess, include_pronouns=include_pronouns),
        ANALYZER.target(target, article_lemma=target_lemma, include_pronouns=include_pronouns),
    )
    return relation in (EXACT.name, LEXEME.name)
