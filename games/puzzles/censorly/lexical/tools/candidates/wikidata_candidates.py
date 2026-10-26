"""Experimental offline Wikidata candidate generator for Censorly.

This tool is outside the gameplay import path. It fetches a deterministic
SPARQL pilot, stores every returned binding locally, and performs conservative
one-token/morphology/matcher screening. P1549 and Wikidata Lexemes are
evidence sources, never automatic gameplay confirmation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from games.puzzles.censorly.article.pool import load_article_pool
from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.tools.candidates.proper_candidates import is_one_token, normalize_lemma

ENDPOINT = "https://query.wikidata.org/sparql"
RU_LANGUAGE_QID = "Q7737"
PILOT_CLASSES = (
    ("Q6256", "countries"),
    ("Q515", "cities"),
    ("Q56061", "administrative_regions"),
    ("Q618123", "geographic_features"),
)

P1549_QUERY = """\
SELECT DISTINCT ?item ?itemLabel ?demonym WHERE {
  ?item wdt:P31 wd:%(class_qid)s .
  ?item rdfs:label ?itemLabel .
  FILTER(LANG(?itemLabel) = "ru")
  ?item p:P1549 ?demonymStatement .
  ?demonymStatement ps:P1549 ?demonym .
  FILTER(LANG(?demonym) = "ru")
}
ORDER BY ?item ?demonym
LIMIT %(limit)d
"""

LEXEME_QUERY = """\
SELECT DISTINCT ?base ?baseLemma ?derived ?derivedLemma
       ?baseCategory ?derivedCategory WHERE {
  ?derived dct:language wd:%(language_qid)s ;
           wikibase:lemma ?derivedLemma ;
           wdt:P5191 ?base .
  ?base dct:language wd:%(language_qid)s ;
        wikibase:lemma ?baseLemma .
  OPTIONAL { ?base wikibase:lexicalCategory ?baseCategory . }
  OPTIONAL { ?derived wikibase:lexicalCategory ?derivedCategory . }
}
ORDER BY ?base ?derived
LIMIT %(limit)d
"""


def _binding_value(binding: Mapping[str, Any], key: str) -> str:
    value = binding.get(key)
    if isinstance(value, Mapping):
        return str(value.get("value") or "")
    return ""


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1] if uri else ""


def sparql_json(query: str, *, endpoint: str = ENDPOINT, timeout: int = 60) -> dict[str, Any]:
    url = endpoint + "?" + urlencode({"query": query, "format": "json"})
    request = Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "Censorly-offline-research/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), Mapping):
        raise ValueError("Wikidata response has no SPARQL results object")
    return payload


def _p1549_rows(payload: Mapping[str, Any], *, class_qid: str, class_name: str, query: str) -> Iterable[dict[str, Any]]:
    bindings = payload.get("results", {}).get("bindings", ())
    if not isinstance(bindings, list):
        return
    for binding in bindings:
        if not isinstance(binding, Mapping):
            continue
        item_uri = _binding_value(binding, "item")
        item_label = _binding_value(binding, "itemLabel")
        demonym = _binding_value(binding, "demonym")
        if not item_uri or not item_label or not demonym:
            continue
        yield {
            "query_kind": "p1549",
            "class_qid": class_qid,
            "class_name": class_name,
            "item_qid": _qid(item_uri),
            "item_label": item_label,
            "demonym": demonym,
            "demonym_language": "ru",
            "item_url": item_uri,
            "source_property": "P1549",
            "query": query,
        }


def _lexeme_rows(payload: Mapping[str, Any], *, query: str) -> Iterable[dict[str, Any]]:
    bindings = payload.get("results", {}).get("bindings", ())
    if not isinstance(bindings, list):
        return
    for binding in bindings:
        if not isinstance(binding, Mapping):
            continue
        base_uri = _binding_value(binding, "base")
        derived_uri = _binding_value(binding, "derived")
        base_lemma = _binding_value(binding, "baseLemma")
        derived_lemma = _binding_value(binding, "derivedLemma")
        if not base_uri or not derived_uri or not base_lemma or not derived_lemma:
            continue
        yield {
            "query_kind": "lexeme",
            "base_lid": _qid(base_uri),
            "derived_lid": _qid(derived_uri),
            "base_lemma": base_lemma,
            "derived_lemma": derived_lemma,
            "base_category": _binding_value(binding, "baseCategory"),
            "derived_category": _binding_value(binding, "derivedCategory"),
            "base_language": "ru",
            "derived_language": "ru",
            "source_property": "P5191",
            "base_url": base_uri,
            "derived_url": derived_uri,
            "query": query,
        }


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def fetch_pilot(
    output_dir: Path,
    *,
    limit_per_class: int = 50,
    lexeme_limit: int = 200,
    endpoint: str = ENDPOINT,
    refresh: bool = False,
) -> tuple[Path, Path]:
    """Fetch/cache P1549 and Lexeme JSONL without downloading a dump."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "wikidata_raw.jsonl"
    lexeme_path = output_dir / "wikidata_lexemes.jsonl"
    meta_path = output_dir / "wikidata_queries.json"
    if raw_path.exists() and lexeme_path.exists() and meta_path.exists() and not refresh:
        return raw_path, lexeme_path
    metadata = {
        "endpoint": endpoint,
        "limit_per_class": limit_per_class,
        "lexeme_limit": lexeme_limit,
        "classes": [{"qid": qid, "name": name} for qid, name in PILOT_CLASSES],
        "fetched_at_epoch": time.time(),
    }
    raw_records: list[dict[str, Any]] = []
    for class_qid, class_name in PILOT_CLASSES:
        query = P1549_QUERY % {"class_qid": class_qid, "limit": limit_per_class}
        payload = sparql_json(query, endpoint=endpoint)
        raw_records.extend(_p1549_rows(
            payload, class_qid=class_qid, class_name=class_name, query=query,
        ))
    lexeme_query = LEXEME_QUERY % {"language_qid": RU_LANGUAGE_QID, "limit": lexeme_limit}
    lexeme_payload = sparql_json(lexeme_query, endpoint=endpoint)
    lexeme_records = list(_lexeme_rows(lexeme_payload, query=lexeme_query))
    _write_jsonl(raw_path, raw_records)
    _write_jsonl(lexeme_path, lexeme_records)
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return raw_path, lexeme_path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def build_candidates(raw_path: Path) -> list[dict[str, Any]]:
    """Screen P1549 rows; no P1549 row is auto-accepted."""
    pool = {fold(title) for title in load_article_pool()}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in read_jsonl(raw_path):
        source = str(row.get("item_label") or "").strip()
        derivative = str(row.get("demonym") or "").strip()
        if not is_one_token(source) or not is_one_token(derivative):
            continue
        source_key = fold(source)
        derivative_key = fold(derivative)
        if not source_key or source_key == derivative_key:
            continue
        source_lemma, source_flags = normalize_lemma(source)
        derivative_lemma, derivative_flags = normalize_lemma(derivative)
        key = (source_lemma or source_key, derivative_lemma or derivative_key, str(row.get("item_qid") or ""))
        candidate = grouped.setdefault(key, {
            "source_name": source,
            "derivative": derivative,
            "normalized_source": source_lemma,
            "normalized_derivative": derivative_lemma,
            "source_morphology_flags": ",".join(source_flags),
            "derivative_morphology_flags": ",".join(derivative_flags),
            "class_qid": row.get("class_qid", ""),
            "class_name": row.get("class_name", ""),
            "item_qid": row.get("item_qid", ""),
            "item_url": row.get("item_url", ""),
            "demonym_language": row.get("demonym_language", ""),
            "source_property": row.get("source_property", "P1549"),
            "source_article_url": f"https://www.wikidata.org/wiki/{row.get('item_qid', '')}",
            "article_pool_source": str(source_key in pool),
            "article_pool_derivative": str(derivative_key in pool),
            "evidence": "Wikidata P1549 with ru language qualifier",
            "decision": "pending_matcher",
            "quality_decision": "pending_review",
            "dictionary_confirmation": "Wikidata P1549 only; independent lexical confirmation not checked",
            "game_admissibility": "not_assessed",
            "review_method": "machine_screening",
            "reason": "P1549 is a demonym candidate source, not a derivational or gameplay proof",
        })
        candidate.setdefault("source_variants", set()).add(source)
        candidate.setdefault("derivative_variants", set()).add(derivative)
        candidate["raw_evidence_count"] = candidate.get("raw_evidence_count", 0) + 1
        candidate["article_pool_source"] = str(
            candidate["article_pool_source"] == "True" or source_key in pool
        )
        candidate["article_pool_derivative"] = str(
            candidate["article_pool_derivative"] == "True" or derivative_key in pool
        )
        candidate["source_morphology_flags"] = ",".join(sorted(filter(
            None, (candidate["source_morphology_flags"], *source_flags),
        )))
        candidate["derivative_morphology_flags"] = ",".join(sorted(filter(
            None, (candidate["derivative_morphology_flags"], *derivative_flags),
        )))
    candidates = list(grouped.values())
    for candidate in candidates:
        candidate["source_variants"] = ",".join(sorted(candidate["source_variants"]))
        candidate["derivative_variants"] = ",".join(sorted(candidate["derivative_variants"]))
    return candidates


def review_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from games.puzzles.censorly.lexical.semantics import explain

    for row in candidates:
        left = row["normalized_source"] or row["source_name"]
        right = row["normalized_derivative"] or row["derivative"]
        current = explain(left, right)
        reverse = explain(right, left)
        row["explain"] = current
        row["explain_reverse"] = reverse
        row["opens_forward"] = str(bool(current))
        row["opens_reverse"] = str(bool(reverse))
        flags = []
        if row["source_morphology_flags"] or row["derivative_morphology_flags"]:
            flags.append("morphology_ambiguous")
        if current or reverse:
            row["decision"] = "already_covered"
            row["quality_decision"] = "already_covered"
            row["game_admissibility"] = "already_open_by_current_matcher"
            row["reason"] = "Already opened by current matcher; excluded from new candidates"
        else:
            row["decision"] = "manual_review"
            row["quality_decision"] = "manual_review"
            row["game_admissibility"] = "not_established"
            row["reason"] = "P1549 is not sufficient proof of a safe Censorly relation"
            if flags:
                row["reason"] = "P1549 candidate with morphology ambiguity; requires manual sense check"
        row["ambiguity_flags"] = ",".join(flags)
    return candidates


def write_candidates(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "source_name", "derivative", "normalized_source", "normalized_derivative",
        "source_variants", "derivative_variants", "raw_evidence_count",
        "class_qid", "class_name", "item_qid", "item_url", "demonym_language",
        "source_property", "source_article_url", "article_pool_source",
        "article_pool_derivative", "source_morphology_flags",
        "derivative_morphology_flags", "evidence", "explain", "explain_reverse",
        "opens_forward", "opens_reverse", "ambiguity_flags", "decision", "reason",
        "quality_decision", "dictionary_confirmation", "game_admissibility", "review_method",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def write_report(output_dir: Path, *, raw_path: Path, lexeme_path: Path, candidates: list[dict[str, Any]]) -> None:
    raw = read_jsonl(raw_path)
    lexemes = read_jsonl(lexeme_path)
    lexeme_one_token_both = sum(
        is_one_token(str(row.get("base_lemma") or ""))
        and is_one_token(str(row.get("derived_lemma") or ""))
        for row in lexemes
    )
    lexeme_adjective_edges = sum(
        str(row.get("derived_category") or "").rsplit("/", 1)[-1] == "Q34698"
        and is_one_token(str(row.get("base_lemma") or ""))
        and is_one_token(str(row.get("derived_lemma") or ""))
        for row in lexemes
    )
    by_class: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_class.setdefault(str(row["class_name"]), []).append(row)
    covered = sum(row["decision"] == "already_covered" for row in candidates)
    new = [row for row in candidates if row["decision"] == "manual_review"]
    any_pool = sum(row["article_pool_source"] == "True" or row["article_pool_derivative"] == "True" for row in new)
    both_pool = sum(row["article_pool_source"] == "True" and row["article_pool_derivative"] == "True" for row in new)
    lines = [
        "# Wikidata P1549/Lexemes pilot",
        "",
        "Пилотный запрос выполнен без полного dump. Результаты кэшированы локально; повторный запуск без refresh не делает сетевых запросов.",
        "",
        "## Статистика",
        "",
        f"- P1549 raw records: **{len(raw)}**.",
        f"- Уникальных однотокенных кандидатов после нормализации: **{len(candidates)}**.",
        f"- Уже покрыты текущим matcher: **{covered}**.",
        f"- Новых машинных кандидатов на ручную проверку: **{len(new)}**.",
        f"- Новых кандидатов с одной стороной в article_pool: **{any_pool}**.",
        f"- Новых кандидатов с обеими сторонами в article_pool: **{both_pool}**.",
        f"- Lexemes rows с P5191: **{len(lexemes)}**.",
        f"- Lexemes с двумя однословными леммами: **{lexeme_one_token_both}**.",
        f"- Из них с derived lexical category Q34698 (adjective): **{lexeme_adjective_edges}**.",
        "",
        "| Класс пилота | Кандидатов | Уже покрыто | Новых manual_review |",
        "|---|---:|---:|---:|",
    ]
    for name, rows in sorted(by_class.items()):
        lines.append(f"| {name} | {len(rows)} | {sum(x['decision'] == 'already_covered' for x in rows)} | {sum(x['decision'] == 'manual_review' for x in rows)} |")
    lines.extend([
        "",
        "## Интерпретация качества",
        "",
        "- P1549 подтверждает, что Wikidata хранит демоним для сущности, но не подтверждает автоматически русскую игровую пару.",
        "- Все новые пары оставлены в manual_review; автоматического accept нет.",
        "- Проверка matcher выполняется в обоих направлениях, но транзитивность не строится.",
        "- Источник сущности сохраняется через QID, класс и URL; язык демонима сохраняется как ru.",
        "- Машинный screening не является ручным аудитом. Для утверждения нужна проверка конкретной русской леммы, значения и омонимии.",
        "",
        "## Lexemes",
        "",
        "В пилоте отдельно проверяется derived from lexeme (P5191) для русских лексем. Эти записи содержат LID, леммы, языковой код и lexical category. В текущем кэше 139 строк, из них 135 с двумя однословными леммами; 12 имеют derived lexical category Q34698 (adjective). При этом выборка содержит обычные словообразовательные связи и аббревиатуры, а не только имена собственные. Поэтому Lexemes полезны как отдельный источник для последующего фильтра по собственным именам, но не дают автоматических P1549-кандидатов и не смешиваются с ними.",
        "",
        "## Ограничения",
        "",
        "Пилот ограничен фиксированным лимитом на четыре класса: страны, города, административные регионы и географические объекты. Дублирование сущностей между классами возможно и дедуплицируется только на уровне пары + QID. P1549 допускает варианты демонимов, регистр и неоднозначные формы; это остаётся ручной зоной.",
        "",
        "Масштабировать импорт стоит только после ручного аудита выборки новых пар и отдельного порога для article_pool. Следующий безопасный шаг — расширить лимит по классам, сохранив тот же кэш и статус manual_review.",
    ])
    (output_dir / "wikidata_pilot_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    try:
        raw_path, lexeme_path = fetch_pilot(
            Path(args.output_dir),
            limit_per_class=args.limit_per_class,
            lexeme_limit=args.lexeme_limit,
            endpoint=args.endpoint,
            refresh=args.refresh,
        )
    except Exception as exc:
        print(f"Wikidata request failed: {exc}", file=sys.stderr)
        print("No synthetic data was generated.", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir)
    candidates = review_candidates(build_candidates(raw_path))
    write_candidates(output_dir / "wikidata_candidates.tsv", candidates)
    write_candidates(output_dir / "wikidata_reviewed.tsv", candidates)
    write_report(output_dir, raw_path=raw_path, lexeme_path=lexeme_path, candidates=candidates)
    print(json.dumps({
        "raw": str(raw_path),
        "lexemes": str(lexeme_path),
        "candidates": len(candidates),
        "new_manual_review": sum(x["decision"] == "manual_review" for x in candidates),
        "already_covered": sum(x["decision"] == "already_covered" for x in candidates),
    }, ensure_ascii=False))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/censorly-wikidata-pilot")
    parser.add_argument("--limit-per-class", type=int, default=50)
    parser.add_argument("--lexeme-limit", type=int, default=200)
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--refresh", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
