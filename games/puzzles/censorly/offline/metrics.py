"""Censorly matching metrics: coverage by word group and correctness.

Offline only; gameplay never imports this. Two questions, kept apart:

* **Coverage** — what share of article tokens has a game root (or opens
  through an alias / hyphen structure), split by word group: common
  vocabulary, proper names, words pymorphy does not know, and short tokens
  (letters, abbreviations). Measured on the main corpus and on the holdout,
  which no review queue reads.
* **Correctness** — of the openings players actually meet, what share is
  right. A labeled random sample of ``guess -> opened word`` pairs, drawn
  from the holdout weighted by corpus frequency and stratified by the
  weakest link of the pair, is re-run through the live matcher. A second,
  independent check is the hand-set school-root grouping of one whole
  article (``bee.roots.json``): precision and recall over all its pairs.

Coverage counts rooted tokens, so it cannot see a wrong root; only the
correctness metrics can.
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from games.puzzles.censorly.lexical.core import fold
from games.puzzles.censorly.lexical.match import ANALYZER, MATCHER, Word
from games.puzzles.censorly.lexical.match.rootbank import provenance, structures_of
from games.puzzles.censorly.offline.corpus import Corpus
from games.puzzles.core.norm_matcher import MORPH_ANALYZER

DATA_DIR = Path(__file__).resolve().parents[1] / 'lexical' / 'data' / 'metrics'
GOLD_PATH = (
    Path(__file__).resolve().parents[3] / 'tests' / 'censorly_testdata' / 'articles' / 'bee.roots.json'
)
GOLD_ARTICLE = GOLD_PATH.with_name('bee.txt')

WORD_GROUPS = ('common', 'proper', 'unknown', 'short')
_PROPER_TAGS = frozenset({'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'})
_GAP_SOURCES = frozenset({'ABSENT', 'UNRESOLVED', 'UNRESOLVED_TOO_AMBIGUOUS'})

# Relations that only say "the same word"; they are not openings to judge.
SAME_WORD = frozenset({'exact', 'lexeme'})
# Root links are grouped by the least reviewed side of the pair.
_SOURCE_BUCKET = {
    'KUZNETSOVA': 'kuznetsova',
    'KUZNETSOVA_MULTI': 'kuznetsova',
    'SEMANTIC_SPLIT_ROOTED': 'kuznetsova',
    'SEMANTIC_ROOT_ASSIGNMENT': 'kuznetsova',
    'AUTHORIZED_ALTERNATIVES': 'kuznetsova',
    'REVIEWED_DERIVED_ROOT': 'reviewed_row',
    'REVIEWED_EXISTING_ROOT': 'reviewed_row',
    'NUMERAL_CARDINAL_ROOT': 'reviewed_row',
    'NUMERAL_DIGIT_ROOT': 'reviewed_row',
    'HALF_COMPOUND': 'reviewed_row',
    'REVIEWED_OPAQUE_ROOT': 'opaque',
    'REVIEWED_NEST_ANCHOR': 'nest_anchor',
    'ADVERB_OF_ADJECTIVE': 'rule',
    'NEGATION_OF_BASE': 'rule',
    'UNIQUE_SPELLING_FALLBACK': 'fallback',
}
# Lower is weaker; a pair takes its weakest side.
_TRUST = ('fallback', 'reading', 'rule', 'nest_anchor', 'opaque', 'reviewed_row', 'kuznetsova')
LINK_GROUPS = _TRUST + ('alias', 'proper', 'reviewed_lexical', 'grammar')


def is_russian(lemma: str) -> bool:
    return not lemma.startswith('__censorly_') and bool(re.search('[а-яё]', lemma))


@lru_cache(maxsize=None)
def word_group(lemma: str) -> str:
    """common | proper | unknown | short, for a Cyrillic corpus lemma."""
    parse = MORPH_ANALYZER.parse(lemma)[0]
    grammemes = set(parse.tag.grammemes)
    if len(lemma) <= 2 or 'Abbr' in grammemes:
        return 'short'
    if not MORPH_ANALYZER.word_is_known(lemma):
        return 'unknown'
    if _PROPER_TAGS & grammemes:
        return 'proper'
    return 'common'


# --- coverage -------------------------------------------------------------

@dataclass
class Coverage:
    tokens: Counter = field(default_factory=Counter)   # group -> Russian tokens
    rooted: Counter = field(default_factory=Counter)   # group -> rooted tokens
    linked: Counter = field(default_factory=Counter)   # group -> tokens another word opens

    def share(self, group: str | None = None, *, linked: bool = False) -> float:
        hits = self.linked if linked else self.rooted
        if group is None:
            return sum(hits.values()) / max(sum(self.tokens.values()), 1)
        return hits[group] / max(self.tokens[group], 1)


def coverage(corpus: Corpus, *, linked: bool = True) -> Coverage:
    """Same rooted test as ``coverage_report``, split by word group.

    ``linked`` also counts tokens that some *other* dictionary word opens.
    A root that links to nothing (a one-word compound, a lone loanword)
    raises ``rooted`` but not ``linked``, and only ``linked`` is something a
    player can notice.
    """
    from games.puzzles.censorly.lexical.deciders.layers import ALIAS
    from games.puzzles.censorly.offline.tools.coverage_report import _opens_at_runtime

    prov = provenance()
    alias_words = frozenset(word for group in ALIAS.groups for word in group)
    result = Coverage()
    for lemma, count in corpus.tokens.items():
        if not is_russian(lemma):
            continue
        group = word_group(lemma)
        result.tokens[group] += count
        source = (prov.get(lemma) or {}).get('source', 'ABSENT')
        if source not in _GAP_SOURCES or _opens_at_runtime(
            lemma, corpus.surfaces.get(lemma) or lemma, alias_words,
        ):
            result.rooted[group] += count
        if linked and is_linked(lemma):
            result.linked[group] += count
    return result


# --- correctness: drawing pairs -------------------------------------------

def _root_ids(lemma: str) -> set[str]:
    return {
        item for structure in structures_of(lemma) for item in structure
        if not item.startswith('sense:')
    }


@lru_cache(maxsize=1)
def _openers_index() -> dict[str, tuple[str, ...]]:
    """Every game root id (and proper / alias component) -> its lemmas."""
    from games.puzzles.censorly.lexical.deciders.layers import ALIAS, PROPER

    index = defaultdict(set)
    for lemma in provenance():
        for root in _root_ids(lemma):
            index[root].add(lemma)
    for word, component in PROPER.component_of.items():
        index[f'proper:{component}'].add(word)
    for number, group in enumerate(ALIAS.groups):
        index[f'alias:{number}'].update(group)
    return {key: tuple(sorted(value)) for key, value in index.items()}


@lru_cache(maxsize=1)
def _keys_by_lemma() -> dict[str, tuple[str, ...]]:
    keys = defaultdict(list)
    for key, lemmas in _openers_index().items():
        for lemma in lemmas:
            keys[lemma].append(key)
    return {lemma: tuple(value) for lemma, value in keys.items()}


@lru_cache(maxsize=200_000)
def _guess_pack(lemma: str) -> Word:
    return ANALYZER.guess(lemma)


@lru_cache(maxsize=200_000)
def _target_pack(lemma: str) -> Word:
    return ANALYZER.target(lemma, article_lemma=lemma)


def relation(guess: str, target: str) -> str:
    """Live gameplay relation of a guess lemma to an article lemma."""
    return MATCHER.relation(_guess_pack(guess), _target_pack(target))


@lru_cache(maxsize=1)
def _lemmas_by_structure() -> dict[tuple, tuple[str, ...]]:
    index = defaultdict(list)
    for lemma in provenance():
        for structure in structures_of(lemma):
            index[structure].append(lemma)
    return {key: tuple(value) for key, value in index.items()}


_LINK_TRIES = 40


def _live_structures(lemma: str) -> tuple[tuple[str, ...], ...]:
    """Bank structures, or the ones the live pack finds through another
    pymorphy reading when the bank has none (примерно -> примерный)."""
    bank = structures_of(lemma)
    if bank:
        return tuple(bank)
    return tuple(sorted(tuple(root) for root in _target_pack(lemma).roots))


def _live_keys(lemma: str) -> tuple[str, ...]:
    keys = _keys_by_lemma().get(lemma)
    if keys:
        return keys
    return tuple(sorted({
        item for structure in _live_structures(lemma) for item in structure
        if not item.startswith('sense:')
    }))


@lru_cache(maxsize=None)
def is_linked(lemma: str) -> bool:
    """True when another dictionary word opens ``lemma`` in live play.

    Words with the same full structure come first (they almost always open);
    then words sharing a root id, an alias group or a proper-name component,
    up to ``_LINK_TRIES`` checks.
    """
    tried = set()
    for structure in _live_structures(lemma):
        for other in _lemmas_by_structure().get(structure, ())[:8]:
            if other != lemma and other not in tried:
                tried.add(other)
                if opens(other, lemma):
                    return True
    budget = _LINK_TRIES
    for key in _live_keys(lemma):
        for other in _openers_index()[key]:
            if other == lemma or other in tried:
                continue
            tried.add(other)
            if opens(other, lemma):
                return True
            budget -= 1
            if budget <= 0:
                return False
    return False


def orphan_kind(lemma: str) -> str:
    """Why a rooted word links to nothing (for the ``orphans`` queue)."""
    structures = structures_of(lemma)
    if any(item.startswith('sense:') for structure in structures for item in structure):
        return 'lone_sense'        # a sense cluster with no other member that opens it
    if any(len([item for item in structure if not item.startswith('sense:')]) > 1 for structure in structures):
        return 'lone_compound'     # nothing else has the whole structure
    if all(len(_openers_index().get(key, ())) <= 1 for key in _keys_by_lemma().get(lemma, ())):
        return 'lone_root'         # the only word of its root
    return 'blocked'               # same root, but the matcher closes every pair


@lru_cache(maxsize=1)
def deliberate_orphans() -> frozenset[str]:
    """Rooted words left unlinked on purpose (reviewed lone senses).

    They still count as unlinked in ``linked`` coverage: a player meets them
    alone either way. Only the ``orphans`` review queue skips them.
    """
    path = DATA_DIR / 'deliberate_orphans.tsv'
    if not path.exists():
        return frozenset()
    with path.open(encoding='utf-8', newline='') as stream:
        return frozenset(row['lemma'] for row in csv.DictReader(stream, delimiter='\t'))


def orphans(corpus: Corpus, count: int) -> list[tuple[int, str, str]]:
    rows = []
    skip = deliberate_orphans()
    for lemma, tokens in corpus.tokens.most_common():
        if not is_russian(lemma) or word_group(lemma) != 'common' or lemma in skip:
            continue
        if structures_of(lemma) and not is_linked(lemma):
            rows.append((tokens, lemma, orphan_kind(lemma)))
            if len(rows) >= count:
                break
    return rows


def opens(guess: str, target: str) -> bool:
    kind = relation(guess, target)
    return bool(kind) and kind not in SAME_WORD


def _bucket(lemma: str) -> str:
    source = (provenance().get(lemma) or {}).get('source', '')
    # No bank root of its own: the live pack found roots through another
    # pymorphy reading (примерно -> примерный).
    return _SOURCE_BUCKET.get(source, 'reading')


def link_group(guess: str, target: str, kind: str | None = None) -> str:
    kind = kind if kind is not None else relation(guess, target)
    if kind in ('alias', 'proper', 'reviewed_lexical', 'grammar'):
        return kind
    sides = (_bucket(guess), _bucket(target))
    return min(sides, key=_TRUST.index)


def _draw_one(rng: random.Random, target: str, weights: Counter, tries: int) -> tuple[str, str] | None:
    keys = _keys_by_lemma().get(target, ())
    candidates = {lemma for key in keys for lemma in _openers_index()[key]}
    candidates.discard(target)
    if not candidates:
        return None
    pool = sorted(candidates)
    chosen = set()
    for _ in range(min(tries, len(pool))):
        guess = rng.choices(pool, weights=[weights.get(item, 0) + 1 for item in pool])[0]
        if guess in chosen:
            continue
        chosen.add(guess)
        kind = relation(guess, target)
        if kind and kind not in SAME_WORD:
            return guess, kind
    return None


def draw_pairs(corpus: Corpus, count: int, seed: int, tries: int = 12) -> list[dict]:
    """``count`` openings: target by token weight, guess by corpus weight."""
    rng = random.Random(seed)
    targets = [lemma for lemma in corpus.tokens if is_russian(lemma) and lemma in _keys_by_lemma()]
    target_weights = [corpus.tokens[lemma] for lemma in targets]
    pairs = []
    attempts = 0
    while len(pairs) < count and attempts < count * 20:
        attempts += 1
        target = rng.choices(targets, weights=target_weights)[0]
        found = _draw_one(rng, target, corpus.tokens, tries)
        if not found:
            continue
        guess, kind = found
        pairs.append({
            'guess': guess,
            'target': target,
            'relation': kind,
            'group': link_group(guess, target, kind),
            'word_group': word_group(target),
        })
    return pairs


def stratified_sample(
    corpus: Corpus, per_group: int, seed: int, pool_size: int, extra: int = 0,
) -> tuple[list[dict], dict[str, float]]:
    """A labeling sample: ``per_group`` pairs per link group, plus ``extra``
    pairs spread in proportion to each group's share of openings, so the
    groups that dominate the overall precision get a narrower interval.

    Returns the sample and each group's share among all drawn openings, so
    group precisions can be re-weighted into one overall precision.
    """
    pool = draw_pairs(corpus, pool_size, seed)
    shares = Counter(pair['group'] for pair in pool)
    weights = {group: count / len(pool) for group, count in shares.items()}
    rng = random.Random(seed + 1)
    by_group = defaultdict(list)
    seen = set()
    for pair in pool:
        key = (pair['guess'], pair['target'])
        if key not in seen:
            seen.add(key)
            by_group[pair['group']].append(pair)
    sample = []
    for group in LINK_GROUPS:
        rows = by_group.get(group, [])
        rng.shuffle(rows)
        sample.extend(rows[:per_group + round(extra * weights.get(group, 0))])
    return sample, weights


# --- correctness: evaluating labels ---------------------------------------

LABEL_FIELDS = ('guess', 'target', 'relation', 'group', 'word_group', 'label', 'note')


def wilson(ok: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if not total:
        return 0.0, 1.0
    p = ok / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Sample:
    path: Path
    meta: dict
    rows: list[dict]


def load_samples() -> list[Sample]:
    samples = []
    for path in sorted(DATA_DIR.glob('correctness_*.tsv')):
        meta_path = path.with_suffix('.meta.json')
        meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
        with path.open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        samples.append(Sample(path, meta, rows))
    return samples


@dataclass
class GroupScore:
    labeled: int = 0          # OK + BAD at labeling time
    ok: int = 0
    open_now: int = 0         # labeled pairs that still open
    ok_now: int = 0
    bad_fixed: int = 0        # BAD pairs that no longer open
    ok_lost: int = 0          # OK pairs that no longer open


def score(sample: Sample) -> dict[str, GroupScore]:
    scores = defaultdict(GroupScore)
    for row in sample.rows:
        label = (row.get('label') or '').upper()
        if label not in ('OK', 'BAD'):
            continue
        group = row['group']
        item = scores[group]
        item.labeled += 1
        item.ok += label == 'OK'
        now = opens(row['guess'], row['target'])
        if now:
            item.open_now += 1
            item.ok_now += label == 'OK'
        elif label == 'BAD':
            item.bad_fixed += 1
        else:
            item.ok_lost += 1
    return dict(scores)


def weighted_precision(scores: dict[str, GroupScore], weights: dict[str, float], now: bool) -> float:
    total = 0.0
    mass = 0.0
    for group, weight in weights.items():
        item = scores.get(group)
        if not item:
            continue
        denom = item.open_now if now else item.labeled
        if not denom:
            continue
        total += weight * (item.ok_now if now else item.ok) / denom
        mass += weight
    return total / mass if mass else 0.0


# --- correctness: the hand-set gold article --------------------------------

@dataclass
class GoldScore:
    lemmas: int
    gold_pairs: int
    opened_pairs: int
    true_pairs: int
    false_opens: list[tuple[str, str]]
    missed: list[tuple[str, str]]

    @property
    def precision(self) -> float:
        return self.true_pairs / max(self.opened_pairs, 1)

    @property
    def recall(self) -> float:
        return self.true_pairs / max(self.gold_pairs, 1)


def gold_article() -> GoldScore:
    """Ordered pairs of the article's content lemmas against school roots."""
    from games.puzzles.censorly.text.tokenize import build_puzzle_payload

    gold = json.loads(GOLD_PATH.read_text(encoding='utf-8'))
    opaque = set(gold.get('opaque', ()))
    payload = build_puzzle_payload(
        wiki_title=gold['article'], body_text=GOLD_ARTICLE.read_text(encoding='utf-8'),
    )
    lemmas = sorted({
        token['lemma'] for part in ('title_tokens', 'body_tokens') for token in payload[part]
        if token.get('kind') == 'content' and token.get('lemma') and token['lemma'] not in opaque
    })
    # Game policy, not the gold file's, decides two things: digits are not
    # article words of the gold (1 -> один is a deliberate game link), and a
    # compound opens only with its whole structure (вода does not open
    # пчеловод), so a pair with a compound side expects identical root sets.
    lemmas = [lemma for lemma in lemmas if is_russian(lemma)]
    roots = defaultdict(set)
    for group in gold['groups']:
        for lemma in group['lemmas']:
            roots[lemma].add(group['id'])
    gold_pairs = opened = true = 0
    false_opens, missed = [], []
    for guess in lemmas:
        for target in lemmas:
            if guess == target:
                continue
            left, right = roots.get(guess, {guess}), roots.get(target, {target})
            if len(left) > 1 or len(right) > 1:
                expected = left == right
            else:
                expected = bool(left & right)
            kind = relation(guess, target)
            got = bool(kind)
            gold_pairs += expected
            opened += got
            if got and expected:
                true += 1
            elif got:
                false_opens.append((guess, target))
            elif expected:
                missed.append((guess, target))
    return GoldScore(len(lemmas), gold_pairs, opened, true, false_opens, missed)
