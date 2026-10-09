"""Find reviewable proper-name derivation candidates from Wiktextract JSONL.

This module is deliberately outside the gameplay import path.  It produces
reports only; it never edits ``proper_names.py`` or the graph used at runtime.
The extractor follows direct dictionary evidence and does not compute a
transitive closure of any relation.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
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
    'организация', 'компания', 'бренд', 'торговая марка', 'организм',
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


def _entry_text(entry: Mapping[str, Any]) -> str:
    parts: list[str] = []
    # Etymology is evidence for a relation, not evidence that the current
    # entry itself is a proper name.  Including it here would classify
    # ``гуглить`` as a company merely because its etymology mentions Google.
    for key in ('word', 'title', 'lang', 'lang_code', 'pos'):
        value = entry.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ('categories', 'tags'):
        value = entry.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
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


def classify_entry(entry: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Classify only explicit proper-name signals; spelling is not enough."""
    text = _entry_text(entry)
    signals: list[str] = []
    if any(marker in text for marker in _GEO_SIGNALS):
        signals.append('geographic')
    if any(marker in text for marker in _PERSON_SIGNALS):
        signals.append('person')
    if any(marker in text for marker in _ORG_SIGNALS):
        signals.append('organization_or_brand')
    if 'имя собственное' in text or 'proper noun' in text or 'proper names' in text:
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
    if field == 'derived' and source_kind:
        return 'direct_derivation', 'high', 'low', ()
    if explicit and source_kind:
        if source_kind == 'organization_or_brand':
            return 'brand_derived', 'high', 'medium', ()
        return 'etymological_derivation', 'high', 'low', ()
    if field == 'related':
        return 'related_not_derivation', 'low', 'high', ('related_is_not_proof',)
    return 'unclassified_relation', 'low', 'high', ('missing_proper_name_signal',)


def _candidate_category(source_kind: str, source_entry: Mapping[str, Any], derivative_entry: Mapping[str, Any]) -> str:
    source_text = _entry_text(source_entry)
    derivative_text = _entry_text(derivative_entry)
    if source_kind == 'geographic':
        return 'geography'
    if source_kind == 'person':
        return 'person'
    if source_kind == 'organization_or_brand':
        return 'brands' if any(marker in source_text for marker in ('бренд', 'торговая марка', 'brand', 'trademark')) else 'organizations'
    if any(marker in derivative_text for marker in ('учение', 'движение', 'теория', 'наука', 'изм', 'ство')):
        return 'historical_scientific'
    return 'other_proper_name'


def _compact_index_entry(entry: Mapping[str, Any]) -> JsonObject:
    """Retain only metadata needed to classify a reverse-linked proper name."""
    compact: JsonObject = {}
    for key in ('word', 'title', 'lang', 'lang_code', 'pos', 'categories', 'tags'):
        if key in entry:
            compact[key] = entry[key]
    return compact


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
) -> list[Candidate]:
    """Extract direct candidates. No pair is inferred from another pair."""
    normalizer = normalize_func or normalize_lemma
    rows: Iterable[Mapping[str, Any]] = (
        entry for entry in entries if isinstance(entry, Mapping)
    )
    by_word: dict[str, Mapping[str, Any]] = dict(entry_index or {})
    if entry_index is None:
        rows = list(rows)
        for entry in rows:
            word = _entry_word(entry)
            if word and is_one_token(word) and is_russian_entry(entry):
                by_word.setdefault(fold(word), entry)
    keys = _pool_keys(pool_titles)
    if explain_func is None:
        from games.censorly.lexical.semantics import explain as explain_func
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
        current = explain_func(name, derivative) or ''
        if current:
            if stats is not None:
                stats.existing_matcher_relations += 1
            return
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
            ambiguity_flags=tuple(extra) + tuple(signals if len(signals) > 1 else ()),
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

    for entry in rows:
        if not is_russian_entry(entry):
            continue
        current_word = _entry_word(entry)
        if not is_one_token(current_word):
            continue
        current_kind, current_signals = classify_entry(entry)
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
                linked_kind, linked_signals = classify_entry(linked_entry)
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
                linked_kind, linked_signals = classify_entry(linked_entry)
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
    index: dict[str, Mapping[str, Any]] = {}
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
        kind, _signals = classify_entry(entry)
        if kind:
            stats.potential_proper_name_records += 1
            # Only proper-name entries are needed for reverse etymology
            # lookup. Keeping every Wiktextract object would defeat the
            # bounded-memory purpose of the two-pass scan.
            index.setdefault(fold(word), _compact_index_entry(entry))

    candidates = extract_candidates(
        load_jsonl(input_path),
        pool_titles=pool_titles,
        explain_func=explain_func,
        normalize_func=normalize_func,
        entry_index=index,
        stats=stats,
    )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Wiktextract JSONL file')
    parser.add_argument('--jsonl', type=Path, default=Path('candidates.jsonl'))
    parser.add_argument('--tsv', type=Path, default=Path('candidates.tsv'))
    parser.add_argument('--top-tsv', type=Path, default=Path('top_candidates.tsv'))
    parser.add_argument('--stats', type=Path, default=Path('candidates_stats.json'))
    parser.add_argument('--article-pool', type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pool_titles = (
        tuple(line.strip() for line in args.article_pool.open(encoding='utf-8') if line.strip())
        if args.article_pool is not None
        else load_article_pool()
    )
    candidates, stats = run_experiment(args.input, pool_titles=pool_titles)
    write_reports(candidates, args.jsonl, args.tsv)
    write_tsv(candidates[:50], args.top_tsv)
    write_stats(stats, args.stats)
    print(f'wrote {len(candidates)} candidates to {args.jsonl} and {args.tsv}')
    print(f'wrote top 50 candidates to {args.top_tsv}; stats to {args.stats}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
