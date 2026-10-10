"""Find reviewable proper-name derivation candidates from Wiktextract JSONL.

This module is deliberately outside the gameplay import path.  It produces
reports only; it never edits ``proper_names.py`` or the graph used at runtime.
The extractor follows direct dictionary evidence and does not compute a
transitive closure of any relation.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import re
import signal
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import quote

try:  # Optional speed-up for multi-million-line offline dumps.
    import ujson as _json
except ImportError:  # pragma: no cover - standard-library fallback
    _json = json

from games.censorly.article_pool import load_article_pool
from games.censorly.lexical.core import fold

_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
_ONE_TOKEN_RE = re.compile(r"^[\w\-‑]+$", re.UNICODE)
_POOL_TITLE_SPLIT_RE = re.compile(r"\s*(?:,|\()")

_GEO_SIGNALS = (
    'топоним', 'город', 'страна', 'регион', 'область', 'край', 'река',
    'озеро', 'море', 'гора', 'остров', 'континент', 'столица', 'провинция',
    'geographic', 'place name', 'city', 'country', 'river', 'lake', 'mountain',
)
_PERSON_SIGNALS = (
    'имя собственное', 'фамил', 'имя человека', 'персоналия', 'учёный',
    'исторический деятель', 'человек', 'surname', 'given name', 'person',
)
_ORG_SIGNALS = (
    'организация', 'компания', 'бренд', 'торговая марка',
    'organization', 'organizations', 'company', 'companies', 'brand', 'trademark',
)
_EXPLICIT_ETYMOLOGY_RE = re.compile(
    r"(?:происходит|образовано|образован|образована|от\s+(?:имени|фамилии|названия)|"
    r"назван(?:о|а|ный|ная)?\s+в\s+честь)",
    re.IGNORECASE,
)

JsonObject = dict[str, Any]
Normalizer = Callable[[str], tuple[str, tuple[str, ...]]]
Explainer = Callable[[str, str], str]
RawSink = Callable[..., None]


@dataclass(frozen=True)
class Candidate:
    source_name: str
    derivative: str
    relation_type: str
    source: str
    source_article: str
    derivative_article: str
    source_article_url: str
    derivative_article_url: str
    category: str
    article_pool_source: bool
    article_pool_derivative: bool
    explain: str
    reason: str
    ambiguity_flags: tuple[str, ...]
    confidence: str
    risk: str
    status: str
    priority: int

    def as_json(self) -> JsonObject:
        data = asdict(self)
        data['ambiguity_flags'] = list(self.ambiguity_flags)
        return data


@dataclass
class ExperimentStats:
    records_total: int = 0
    russian_records: int = 0
    potential_proper_name_records: int = 0
    source_relations: int = 0
    multiline_relations: int = 0
    existing_matcher_relations: int = 0
    new_unique_candidates: int = 0
    confirmed: int = 0
    ambiguous: int = 0
    rejected: int = 0
    pool_any: int = 0
    pool_both: int = 0
    matcher_timeouts: int = 0
    by_category: dict[str, int] | None = None

    def as_json(self) -> JsonObject:
        data = asdict(self)
        data['by_category'] = dict(self.by_category or {})
        return data


def is_one_token(value: str) -> bool:
    """Accept one lexical token; reject spaces, parentheses and wiki titles."""
    value = (value or '').strip()
    return bool(value and _ONE_TOKEN_RE.fullmatch(value))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ('word', 'term', 'lemma', 'title', 'name'):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, (list, tuple)) and value:
        return _text(value[0])
    return ''


def _entry_word(entry: Mapping[str, Any]) -> str:
    return _text(entry.get('word') or entry.get('title')).strip()


def is_russian_entry(entry: Mapping[str, Any]) -> bool:
    """Kaikki's ruwiktionary file contains many languages, not only Russian."""
    code = str(entry.get('lang_code') or '').casefold()
    language = str(entry.get('lang') or '').casefold()
    if not code and not language:
        return True
    return code in ('ru', 'rus') or language in ('русский', 'russian', 'ru')


def _iter_words(value: Any) -> Iterator[tuple[str, str]]:
    """Yield (word, local evidence) from Wiktextract linkage fields."""
    if isinstance(value, str):
        if value.strip():
            yield value.strip(), value.strip()
        return
    if isinstance(value, Mapping):
        word = _text(value).strip()
        if word:
            evidence = str(value.get('sense') or value.get('gloss') or word)
            yield word, evidence
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_words(item)


def _entry_text(entry: Mapping[str, Any], *, include_senses: bool = True) -> str:
    parts: list[str] = []
    # The headword and etymology are not proper-name classification evidence:
    # ``гора`` must not match inside ``агорафобия``, and a derivative must not
    # become a proper name merely because its etymology mentions one.
    for key in ('categories', 'tags'):
        value = entry.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, str):
            parts.append(value)
    if not include_senses:
        return ' '.join(parts).casefold()
    for sense in entry.get('senses') or ():
        if not isinstance(sense, Mapping):
            continue
        for key in ('glosses', 'raw_glosses', 'tags', 'categories'):
            value = sense.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            elif isinstance(value, str):
                parts.append(value)
    return ' '.join(parts).casefold()


def _has_signal(text: str, marker: str) -> bool:
    pattern = r'(?<!\w)' + re.escape(marker.casefold()) + r'(?:\w*)?(?!\w)'
    return bool(re.search(pattern, text.casefold(), re.UNICODE))


def classify_entry(entry: Mapping[str, Any], *, include_senses: bool = True) -> tuple[str, tuple[str, ...]]:
    """Classify only explicit proper-name signals; spelling is not enough."""
    text = _entry_text(entry, include_senses=include_senses)
    signals: list[str] = []
    if any(_has_signal(text, marker) for marker in _GEO_SIGNALS):
        signals.append('geographic')
    if any(_has_signal(text, marker) for marker in _PERSON_SIGNALS):
        signals.append('person')
    if any(_has_signal(text, marker) for marker in _ORG_SIGNALS):
        signals.append('organization_or_brand')
    if (
        _has_signal(text, 'имя собственное')
        or _has_signal(text, 'имена собственные')
        or _has_signal(text, 'proper noun')
        or _has_signal(text, 'proper names')
    ):
        signals.append('proper_noun')
    if not signals:
        return '', ()
    if 'organization_or_brand' in signals:
        return 'organization_or_brand', tuple(sorted(set(signals)))
    if 'geographic' in signals:
        return 'geographic', tuple(sorted(set(signals)))
    if 'person' in signals:
        return 'person', tuple(sorted(set(signals)))
    if len(signals) > 1:
        return 'proper_name', tuple(sorted(set(signals)))
    return signals[0], tuple(signals)


def normalize_lemma(value: str) -> tuple[str, tuple[str, ...]]:
    """Normalize a surface through the project's dictionary when possible."""
    folded = fold(value)
    if not folded:
        return '', ('empty',)
    try:
        from games.censorly.lexical.russian.morphology import readings

        found = readings(value)
    except (ImportError, RuntimeError, ValueError):
        found = ()
    lemmas = {item.lemma for item in found if getattr(item, 'lemma', '')}
    if len(lemmas) == 1:
        return next(iter(lemmas)), ()
    if len(lemmas) > 1:
        return folded, ('morphology_ambiguous',)
    return folded, ('not_in_project_morphology',)


class _MatcherTimeout(Exception):
    pass


_MATCHER_TIMEOUT = 'matcher_timeout'


def _explain_worker(pair: tuple[str, str]) -> str:
    from games.censorly.lexical.semantics import explain
    return explain(pair[0], pair[1]) or ''


def _bounded_explainer(explain_func: Explainer, *, timeout_seconds: float = 0.05) -> Explainer:
    """Call the live matcher without allowing one pathological pair to hang a dump."""
    cache: dict[tuple[str, str], str] = {}

    def call(left: str, right: str) -> str:
        key = (left, right)
        if key in cache:
            return cache[key]

        def alarm(_signum: int, _frame: Any) -> None:
            raise _MatcherTimeout

        previous = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            result = explain_func(left, right) or ''
        except _MatcherTimeout:
            result = _MATCHER_TIMEOUT
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        cache[key] = result
        return result

    return call


def _bounded_normalizer(normalizer: Normalizer, *, timeout_seconds: float = 0.05) -> Normalizer:
    cache: dict[str, tuple[str, tuple[str, ...]]] = {}

    def call(value: str) -> tuple[str, tuple[str, ...]]:
        if value in cache:
            return cache[value]

        def alarm(_signum: int, _frame: Any) -> None:
            raise _MatcherTimeout

        previous = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            result = normalizer(value)
        except _MatcherTimeout:
            result = (fold(value), ('morphology_timeout',))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        cache[value] = result
        return result

    return call


def _pool_keys(titles: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for title in titles:
        raw = (title or '').strip()
        if not raw:
            continue
        keys.add(fold(raw))
        base = _POOL_TITLE_SPLIT_RE.split(raw, maxsplit=1)[0].strip()
        if is_one_token(base):
            keys.add(fold(base))
    return keys


def _explicit_etymology(entry: Mapping[str, Any]) -> str:
    values = [entry.get('etymology_text'), entry.get('etymology'), entry.get('etymology_texts')]
    values.extend(
        item.get('text', '') for item in (entry.get('etymology_templates') or ())
        if isinstance(item, Mapping)
    )
    return ' '.join(
        str(item)
        for value in values
        for item in (value if isinstance(value, list) else [value])
        if isinstance(item, str)
    )


def _etymology_links(entry: Mapping[str, Any]) -> Iterator[str]:
    text = _explicit_etymology(entry)
    seen: set[str] = set()
    for match in _WIKI_LINK_RE.finditer(text):
        word = match.group(1).strip()
        if is_one_token(word) and word not in seen:
            seen.add(word)
            yield word
    for item in entry.get('etymology_links') or ():
        word = _text(item).strip()
        if is_one_token(word) and word not in seen:
            seen.add(word)
            yield word


def _relation_kind(field: str, *, explicit: bool, source_kind: str) -> tuple[str, str, str, tuple[str, ...]]:
    # Wiktextract's ``related`` is a lexical-neighbour list, not a derivation
    # proof.  This must win even when the same entry also has etymology text.
    if field == 'related':
        return 'related_not_derivation', 'low', 'high', ('related_is_not_proof',)
    if field == 'derived' and source_kind:
        return 'direct_derivation', 'high', 'low', ()
    if explicit and source_kind:
        if source_kind == 'organization_or_brand':
            return 'brand_derived', 'high', 'medium', ()
        return 'etymological_derivation', 'high', 'low', ()
    return 'unclassified_relation', 'low', 'high', ('missing_proper_name_signal',)


def _candidate_category(source_kind: str, source_entry: Mapping[str, Any], derivative_entry: Mapping[str, Any]) -> str:
    source_text = _entry_text(source_entry)
    derivative_text = _entry_text(derivative_entry)
    if source_kind == 'geographic':
        return 'geography'
    if source_kind == 'person':
        return 'person'
    if source_kind == 'organization_or_brand':
        return 'brands' if any(_has_signal(source_text, marker) for marker in ('бренд', 'торговая марка', 'brand', 'trademark')) else 'organizations'
    derivative_word = _entry_word(derivative_entry).casefold()
    if any(_has_signal(derivative_text, marker) for marker in ('учение', 'движение', 'теория', 'наука')) or derivative_word.endswith(('изм', 'ство')):
        return 'historical_scientific'
    return 'other_proper_name'


def _compact_index_entry(entry: Mapping[str, Any]) -> JsonObject:
    """Retain only metadata needed to classify a reverse-linked proper name."""
    compact: JsonObject = {}
    for key in ('word', 'title', 'lang', 'lang_code', 'pos', 'categories', 'tags'):
        if key in entry:
            value = entry[key]
            compact[key] = '\x1f'.join(str(item) for item in value) if isinstance(value, list) else value
    return compact


class _ProperSqliteIndex:
    """Disk-backed proper-name metadata index for multi-million-line dumps."""

    def __init__(self, path: Path):
        self._connection = sqlite3.connect(path)
        self._connection.execute('CREATE TABLE entries (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self._pending: list[tuple[str, str]] = []

    def put(self, key: str, entry: Mapping[str, Any]) -> None:
        self._pending.append((key, json.dumps(_compact_index_entry(entry), ensure_ascii=False)))
        if len(self._pending) >= 4096:
            self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        self._connection.executemany(
            'INSERT OR IGNORE INTO entries(key, payload) VALUES (?, ?)', self._pending,
        )
        self._pending.clear()

    def get(self, key: str, default: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        row = self._connection.execute('SELECT payload FROM entries WHERE key = ?', (key,)).fetchone()
        return json.loads(row[0]) if row else (default or {})

    def commit(self) -> None:
        self._flush()
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def _make_candidate(
    *,
    name: str,
    derivative: str,
    relation_type: str,
    source: str,
    source_article: str,
    derivative_article: str,
    pool_keys: set[str],
    explain: str,
    reason: str,
    ambiguity_flags: Iterable[str],
    confidence: str,
    risk: str,
    status: str,
    normalizer: Normalizer,
    category: str,
) -> Candidate | None:
    if not is_one_token(name) or not is_one_token(derivative):
        return None
    left, left_flags = normalizer(name)
    right, right_flags = normalizer(derivative)
    flags = tuple(sorted(set(ambiguity_flags) | set(left_flags) | set(right_flags)))
    if not left or not right or left == right:
        return None
    in_left = fold(left) in pool_keys
    in_right = fold(right) in pool_keys
    priority = (100 if in_left and in_right else 70 if in_left else 40 if in_right else 0)
    if status == 'confirmed':
        priority += 20
    elif status == 'ambiguous':
        priority += 5
    return Candidate(
        source_name=left,
        derivative=right,
        relation_type=relation_type,
        source=source,
        source_article=source_article,
        derivative_article=derivative_article,
        source_article_url='https://ru.wiktionary.org/wiki/' + quote(source_article.replace(' ', '_'), safe='()'),
        derivative_article_url='https://ru.wiktionary.org/wiki/' + quote(derivative_article.replace(' ', '_'), safe='()'),
        category=category,
        article_pool_source=in_left,
        article_pool_derivative=in_right,
        explain=explain,
        reason=reason,
        ambiguity_flags=flags,
        confidence=confidence,
        risk=risk,
        status=status,
        priority=priority,
    )


def extract_candidates(
    entries: Iterable[Mapping[str, Any]],
    *,
    pool_titles: Iterable[str] = (),
    explain_func: Explainer | None = None,
    normalize_func: Normalizer | None = None,
    entry_index: Mapping[str, Mapping[str, Any]] | None = None,
    stats: ExperimentStats | None = None,
    raw_sink: RawSink | None = None,
) -> list[Candidate]:
    """Extract direct candidates. No pair is inferred from another pair."""
    normalizer = normalize_func or normalize_lemma
    raw_normalizer = normalizer
    normalized_cache: dict[str, tuple[str, tuple[str, ...]]] = {}

    def cached_normalizer(value: str) -> tuple[str, tuple[str, ...]]:
        if value not in normalized_cache:
            normalized_cache[value] = raw_normalizer(value)
        return normalized_cache[value]

    normalizer = _bounded_normalizer(cached_normalizer)
    rows: Iterable[Mapping[str, Any]] = (
        entry for entry in entries if isinstance(entry, Mapping)
    )
    by_word: Any = entry_index if entry_index is not None else {}
    if entry_index is None:
        rows = list(rows)
        by_word = {}
        for entry in rows:
            word = _entry_word(entry)
            if word and is_one_token(word) and is_russian_entry(entry):
                by_word.setdefault(fold(word), entry)
    keys = _pool_keys(pool_titles)
    if explain_func is None:
        from games.censorly.lexical.semantics import explain as explain_func
    explain_func = _bounded_explainer(explain_func)
    output: dict[tuple[str, str, str], Candidate] = {}

    def add(
            name: str,
            derivative: str,
            *,
            relation_field: str,
            source_article: str,
            derivative_article: str,
            evidence: str,
            source_kind: str,
            source_entry: Mapping[str, Any],
            derivative_entry: Mapping[str, Any],
            signals: tuple[str, ...],
            explicit: bool = False,
    ) -> None:
        relation_type, confidence, risk, extra = _relation_kind(
            relation_field, explicit=explicit, source_kind=source_kind,
        )
        status = 'confirmed' if confidence == 'high' else 'ambiguous'
        reason = evidence or f'{relation_field} field in Wiktextract entry'
        if relation_field == 'related':
            reason = 'Поле related само по себе не доказывает словообразование'
        if raw_sink is not None:
            raw_sink(
                name=name, derivative=derivative, relation_field=relation_field,
                relation_type=relation_type,
                source_article=source_article, derivative_article=derivative_article,
                evidence=evidence, source_kind=source_kind, signals=signals,
                confidence=confidence, risk=risk, status=status, reason=reason,
            )
            return
        current = explain_func(name, derivative) or ''
        if current and current != _MATCHER_TIMEOUT:
            if stats is not None:
                stats.existing_matcher_relations += 1
            return
        if current == _MATCHER_TIMEOUT and stats is not None:
            stats.matcher_timeouts += 1
        candidate = _make_candidate(
            name=name,
            derivative=derivative,
            relation_type=relation_type,
            source='ru_wiktionary_wiktextract',
            source_article=source_article,
            derivative_article=derivative_article,
            pool_keys=keys,
            explain=current,
            reason=reason,
            ambiguity_flags=tuple(extra) + tuple(signals if len(signals) > 1 else ()) + (
                ('matcher_timeout',) if current == _MATCHER_TIMEOUT else ()
            ),
            confidence=confidence,
            risk=risk,
            status=status,
            normalizer=normalizer,
                category=_candidate_category(source_kind, source_entry, derivative_entry),
        )
        if candidate is not None:
            output[(candidate.source_name, candidate.derivative, candidate.relation_type)] = candidate
        elif stats is not None:
            stats.rejected += 1

    scanned = 0
    for entry in rows:
        scanned += 1
        if scanned % 250_000 == 0:
            print(f'extracting: {scanned} records, {len(output)} candidates', file=sys.stderr, flush=True)
        if not is_russian_entry(entry):
            continue
        current_word = _entry_word(entry)
        if not is_one_token(current_word):
            continue
        if not (entry.get('derived') or entry.get('related') or _explicit_etymology(entry)):
            continue
        current_kind, current_signals = classify_entry(entry, include_senses=False)
        explicit = bool(_EXPLICIT_ETYMOLOGY_RE.search(_explicit_etymology(entry)))

        for field in ('derived', 'related'):
            for linked, evidence in _iter_words(entry.get(field)):
                if stats is not None:
                    stats.source_relations += 1
                if not is_one_token(linked):
                    if stats is not None:
                        stats.multiline_relations += 1
                        stats.rejected += 1
                    continue
                linked_entry = by_word.get(fold(linked), {})
                linked_kind, linked_signals = classify_entry(linked_entry, include_senses=False)
                if current_kind:
                    add(
                        current_word, linked, relation_field=field,
                        source_article=current_word,
                        derivative_article=linked, evidence=evidence,
                        source_kind=current_kind, source_entry=entry,
                        derivative_entry=linked_entry, signals=current_signals,
                        explicit=explicit,
                    )
                elif linked_kind:
                    add(
                        linked, current_word, relation_field=field,
                        source_article=linked,
                        derivative_article=current_word, evidence=evidence,
                        source_kind=linked_kind, source_entry=linked_entry,
                        derivative_entry=entry, signals=linked_signals,
                        explicit=explicit,
                    )
                elif stats is not None:
                    stats.rejected += 1

        if explicit:
            for linked in _etymology_links(entry):
                if stats is not None:
                    stats.source_relations += 1
                linked_entry = by_word.get(fold(linked), {})
                linked_kind, linked_signals = classify_entry(linked_entry, include_senses=False)
                if current_kind and not linked_kind:
                    add(
                        current_word, linked, relation_field='etymology',
                            source_article=current_word,
                        derivative_article=current_word, evidence=_explicit_etymology(entry),
                        source_kind=current_kind, source_entry=entry,
                        derivative_entry=entry, signals=current_signals,
                        explicit=True,
                    )
                elif linked_kind and not current_kind:
                    add(
                        linked, current_word, relation_field='etymology',
                            source_article=linked,
                        derivative_article=current_word, evidence=_explicit_etymology(entry),
                        source_kind=linked_kind, source_entry=linked_entry,
                        derivative_entry=entry, signals=linked_signals,
                        explicit=True,
                    )
                elif stats is not None:
                    stats.rejected += 1
    return sorted(
        output.values(),
        key=lambda row: (-row.priority, row.status, row.source_name, row.derivative),
    )


def load_jsonl(path: Path) -> Iterator[JsonObject]:
    """Read Wiktextract JSONL, skipping blank/malformed/non-object rows."""
    opener = gzip.open if path.name.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = _json.loads(line)
            except (ValueError, json.JSONDecodeError):
                print(f'warning: skipped malformed JSONL line {line_number}', file=sys.stderr)
                continue
            if isinstance(value, dict):
                yield value
            else:
                print(f'warning: skipped non-object JSONL line {line_number}', file=sys.stderr)


_TSV_FIELDS = tuple(Candidate.__dataclass_fields__)


def write_reports(candidates: Iterable[Candidate], jsonl_path: Path, tsv_path: Path) -> None:
    rows = list(candidates)
    with jsonl_path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row.as_json(), ensure_ascii=False, sort_keys=True) + '\n')
    write_tsv(rows, tsv_path)


def write_tsv(rows: Iterable[Candidate], tsv_path: Path) -> None:
    rows = list(rows)
    with tsv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=_TSV_FIELDS, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            data['ambiguity_flags'] = ','.join(row.ambiguity_flags)
            writer.writerow(data)


def run_experiment(
    input_path: Path,
    *,
    pool_titles: Iterable[str],
    explain_func: Explainer | None = None,
    normalize_func: Normalizer | None = None,
) -> tuple[list[Candidate], ExperimentStats]:
    """Run a bounded-memory two-pass scan over a JSONL or JSONL.gz dump."""
    stats = ExperimentStats(by_category={})
    with tempfile.TemporaryDirectory(prefix='censorly-proper-index-') as directory:
        index = _ProperSqliteIndex(Path(directory) / 'proper.sqlite3')
        try:
            for entry in load_jsonl(input_path):
                stats.records_total += 1
                if stats.records_total % 250_000 == 0:
                    print(
                        f'indexing: {stats.records_total} records, {stats.russian_records} Russian',
                        file=sys.stderr,
                        flush=True,
                    )
                if not is_russian_entry(entry):
                    continue
                stats.russian_records += 1
                word = _entry_word(entry)
                if not is_one_token(word):
                    continue
                kind, _signals = classify_entry(entry, include_senses=False)
                if kind:
                    stats.potential_proper_name_records += 1
                    index.put(fold(word), entry)
            index.commit()

            candidates = extract_candidates(
                load_jsonl(input_path),
                pool_titles=pool_titles,
                explain_func=explain_func,
                normalize_func=normalize_func,
                entry_index=index,
                stats=stats,
            )
        finally:
            index.close()
    stats.new_unique_candidates = len(candidates)
    for candidate in candidates:
        if candidate.status == 'confirmed':
            stats.confirmed += 1
        elif candidate.status == 'ambiguous':
            stats.ambiguous += 1
        else:
            stats.rejected += 1
        if candidate.article_pool_source or candidate.article_pool_derivative:
            stats.pool_any += 1
        if candidate.article_pool_source and candidate.article_pool_derivative:
            stats.pool_both += 1
        stats.by_category[candidate.category] = stats.by_category.get(candidate.category, 0) + 1
    return candidates, stats


def write_stats(stats: ExperimentStats, path: Path) -> None:
    path.write_text(json.dumps(stats.as_json(), ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


class _RawStore:
    """Persistent stage A/B store; morphology and matcher never run during import."""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=NORMAL')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS raw_edges (
            source_raw TEXT NOT NULL, derivative_raw TEXT NOT NULL,
            relation_type TEXT NOT NULL, source_article TEXT NOT NULL,
            derivative_article TEXT NOT NULL, evidence TEXT NOT NULL,
            source_kind TEXT NOT NULL, signals TEXT NOT NULL,
            confidence TEXT NOT NULL, risk TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY (source_raw, derivative_raw, relation_type,
                         source_article, derivative_article, evidence)
        )''')
        self.connection.execute('''CREATE INDEX IF NOT EXISTS raw_pair_idx
            ON raw_edges(source_raw, derivative_raw, relation_type)''')
        self.pending: list[tuple[str, ...]] = []

    def add(self, **row: Any) -> None:
        self.pending.append(tuple(str(row[key]) for key in (
            'name', 'derivative', 'relation_type', 'source_article',
            'derivative_article', 'evidence', 'source_kind',
        )) + (
            ','.join(row['signals']), row['confidence'], row['risk'],
            row['status'], row['reason'],
        ))
        if len(self.pending) >= 4096:
            self.flush()

    def flush(self) -> None:
        if self.pending:
            self.connection.executemany(
                'INSERT OR IGNORE INTO raw_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', self.pending,
            )
            self.pending.clear()

    def close(self) -> None:
        self.flush()
        self.connection.commit()
        self.connection.close()


def _parallel_explain(pairs: Iterable[tuple[str, str]]) -> Iterator[str]:
    """Check only unique pairs; workers isolate expensive morphology startup."""
    with ProcessPoolExecutor(max_workers=4) as pool:
        yield from pool.map(_explain_worker, pairs, chunksize=32)


def run_staged_experiment(
    input_path: Path, *, pool_titles: Iterable[str], workdir: Path,
) -> tuple[list[Candidate], ExperimentStats]:
    """Run stages A-E with restartable raw SQLite state.

    The raw stage is deliberately morphology/matcher-free. Only unique pairs
    from the persisted store enter stages C/D.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / 'proper_candidates.sqlite3'
    stats = ExperimentStats(by_category={})
    with tempfile.TemporaryDirectory(prefix='censorly-proper-index-') as directory:
        index = _ProperSqliteIndex(Path(directory) / 'proper.sqlite3')
        try:
            for entry in load_jsonl(input_path):
                stats.records_total += 1
                if stats.records_total % 250_000 == 0:
                    print(f'indexing: {stats.records_total} records, {stats.russian_records} Russian', file=sys.stderr, flush=True)
                if not is_russian_entry(entry):
                    continue
                stats.russian_records += 1
                word = _entry_word(entry)
                if is_one_token(word) and (kind := classify_entry(entry, include_senses=False)[0]):
                    stats.potential_proper_name_records += 1
                    index.put(fold(word), entry)
            index.commit()
            raw = _RawStore(db_path)
            try:
                extract_candidates(
                    load_jsonl(input_path), pool_titles=pool_titles,
                    entry_index=index, stats=stats,
                    explain_func=lambda _a, _b: '',
                    normalize_func=lambda value: (value, ()), raw_sink=raw.add,
                )
            finally:
                raw.close()
        finally:
            index.close()

    candidates: list[Candidate] = []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    normalizer_cache: dict[str, tuple[str, tuple[str, ...]]] = {}
    raw_normalizer = normalize_lemma
    def norm(value: str) -> tuple[str, tuple[str, ...]]:
        if value not in normalizer_cache:
            normalizer_cache[value] = raw_normalizer(value)
        return normalizer_cache[value]
    from games.censorly.lexical.semantics import explain as live_explain
    explain_cache = _bounded_explainer(live_explain)
    pool_keys = _pool_keys(pool_titles)
    query = '''SELECT source_raw, derivative_raw, relation_type,
        MIN(source_article) source_article, MIN(derivative_article) derivative_article,
        GROUP_CONCAT(reason, ' | ') reason, MIN(source_kind) source_kind,
        MIN(confidence) confidence, MIN(risk) risk, MIN(status) status
        FROM raw_edges GROUP BY source_raw, derivative_raw, relation_type'''
    rows = list(con.execute(query))
    normalized_rows: list[tuple[sqlite3.Row, str, str, tuple[str, ...]]] = []
    for row in rows:
        left, left_flags = norm(row['source_raw'])
        right, right_flags = norm(row['derivative_raw'])
        if not left or not right or left == right:
            stats.rejected += 1
            continue
        normalized_rows.append((row, left, right, tuple(sorted(set(left_flags) | set(right_flags)))))
    explain_results = iter(_parallel_explain((left, right) for _row, left, right, _flags in normalized_rows))
    for row, left, right, morph_flags in normalized_rows:
        current = next(explain_results)
        if current and current != _MATCHER_TIMEOUT:
            stats.existing_matcher_relations += 1
            continue
        flags = tuple(sorted(set(morph_flags) | ({'matcher_timeout'} if current == _MATCHER_TIMEOUT else set())))
        category = _candidate_category(row['source_kind'], {'word': row['source_raw']}, {'word': row['derivative_raw']})
        in_left, in_right = fold(left) in pool_keys, fold(right) in pool_keys
        status = row['status'] if row['relation_type'] != 'related' else 'ambiguous'
        confidence = row['confidence']
        priority = 100 if in_left and in_right else 70 if in_left else 40 if in_right else 0
        priority += 20 if status == 'confirmed' else 5
        candidates.append(Candidate(
            source_name=left, derivative=right, relation_type=row['relation_type'],
            source='ru_wiktionary_wiktextract', source_article=row['source_article'],
            derivative_article=row['derivative_article'],
            source_article_url='https://ru.wiktionary.org/wiki/' + quote(row['source_article'].replace(' ', '_'), safe='()'),
            derivative_article_url='https://ru.wiktionary.org/wiki/' + quote(row['derivative_article'].replace(' ', '_'), safe='()'),
            category=category, article_pool_source=in_left, article_pool_derivative=in_right,
            explain=current, reason=row['reason'], ambiguity_flags=flags,
            confidence=confidence, risk=row['risk'], status=status, priority=priority,
        ))
    con.close()
    candidates.sort(key=lambda r: (-r.priority, r.status, r.source_name, r.derivative))
    stats.new_unique_candidates = len(candidates)
    for row in candidates:
        if row.status == 'confirmed': stats.confirmed += 1
        elif row.status == 'ambiguous': stats.ambiguous += 1
        if row.article_pool_source or row.article_pool_derivative: stats.pool_any += 1
        if row.article_pool_source and row.article_pool_derivative: stats.pool_both += 1
        stats.by_category[row.category] = stats.by_category.get(row.category, 0) + 1
    return candidates, stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Wiktextract JSONL file')
    parser.add_argument('--jsonl', type=Path, default=Path('candidates.jsonl'))
    parser.add_argument('--tsv', type=Path, default=Path('candidates.tsv'))
    parser.add_argument('--top-tsv', type=Path, default=Path('top_candidates.tsv'))
    parser.add_argument('--stats', type=Path, default=Path('candidates_stats.json'))
    parser.add_argument('--article-pool', type=Path, default=None)
    parser.add_argument('--workdir', type=Path, default=None, help='Persistent offline stage directory')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pool_titles = (
        tuple(line.strip() for line in args.article_pool.open(encoding='utf-8') if line.strip())
        if args.article_pool is not None
        else load_article_pool()
    )
    if args.workdir is not None:
        candidates, stats = run_staged_experiment(args.input, pool_titles=pool_titles, workdir=args.workdir)
    else:
        candidates, stats = run_experiment(args.input, pool_titles=pool_titles)
    write_reports(candidates, args.jsonl, args.tsv)
    write_tsv(candidates[:50], args.top_tsv)
    write_stats(stats, args.stats)
    print(f'wrote {len(candidates)} candidates to {args.jsonl} and {args.tsv}')
    print(f'wrote top 50 candidates to {args.top_tsv}; stats to {args.stats}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
