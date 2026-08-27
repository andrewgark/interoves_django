#!/usr/bin/env python3
"""Build the offline dictionary used for non-theme Salad discoveries.

The old RNC list is the trusted base.  New words are taken only from known
OpenCorpora lexemes (never pymorphy's unknown-word predictions) and must have
at least one observed Nutrimatic frequency.  The latter is a ranking/evidence
signal, not a hard frequency cutoff: ``--min-frequency`` defaults to 1.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path

import pymorphy3

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD = ROOT / "games/word_salad_nouns.txt"
DEFAULT_OUT = ROOT / "games/salad_valid_words.txt.gz"
DEFAULT_REPORT = ROOT / "games/salad_valid_words.tsv"
DEFAULT_NUTRIMATIC = ROOT.parent / "nutrimatic-ru/scripts/output/russian_words.txt"
DEFAULT_WIKTIONARY = ROOT.parent / "experiments/dictionaries/ru_wiktionary.txt"

CYRILLIC = re.compile(r"^[а-я]+$")
PROPER = frozenset({"Name", "Surn", "Patr", "Geox", "Orgn", "Trad", "Init", "Abbr"})


def norm(value: str) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def load_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {norm(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def load_nutrimatic(path: Path) -> dict[str, int]:
    """Aggregate counts by a known, canonical common-noun lemma."""
    if not path.is_file():
        return {}
    morph = pymorphy3.MorphAnalyzer()
    counts: dict[str, int] = defaultdict(int)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw_count, raw_word = line.split("\t", 1)
            count = int(raw_count)
        except (ValueError, TypeError):
            continue
        word = norm(raw_word)
        if not CYRILLIC.fullmatch(word):
            continue
        # parse() is used only after the source has supplied an observed word;
        # acceptance below still requires that the same word is a known
        # OpenCorpora lexeme.  This avoids treating pymorphy's predictions as
        # dictionary entries.
        parse = morph.parse(word)[0]
        if parse.tag.POS != "NOUN" or PROPER.intersection(parse.tag.grammemes):
            continue
        if "nomn" not in parse.tag or norm(parse.normal_form) != word:
            continue
        counts[word] += count
    return dict(counts)


def known_opencorpora_nouns() -> dict[str, str]:
    """Return canonical nouns from iter_known_words(), not parse() guesses."""
    morph = pymorphy3.MorphAnalyzer()
    result: dict[str, str] = {}
    for word, tag, normal_form, _para_id, _idx in morph.dictionary.iter_known_words():
        word_n = norm(word)
        lemma = norm(normal_form)
        if word_n != lemma or not CYRILLIC.fullmatch(lemma):
            continue
        if tag.POS != "NOUN" or "nomn" not in tag:
            continue
        if PROPER.intersection(tag.grammemes):
            continue
        result.setdefault(lemma, str(tag))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--nutrimatic", type=Path, default=DEFAULT_NUTRIMATIC)
    parser.add_argument("--wiktionary", type=Path, default=DEFAULT_WIKTIONARY)
    parser.add_argument("--allow", type=Path)
    parser.add_argument("--block", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-frequency", type=int, default=1)
    args = parser.parse_args()

    old = load_lines(args.old)
    allow = load_lines(args.allow) if args.allow else set()
    block = load_lines(args.block) if args.block else set()
    frequencies = load_nutrimatic(args.nutrimatic)
    oc = known_opencorpora_nouns()
    wiki = load_lines(args.wiktionary)

    accepted = set(old) | allow
    report: list[dict[str, object]] = []
    for word, tags in oc.items():
        frequency = frequencies.get(word, 0)
        is_old = word in old
        length_ok = len(word) >= 4 or is_old
        wiktionary = word in wiki
        # OpenCorpora + observed corpus evidence is the conservative new-word
        # rule.  Wiktionary is retained as a diagnostic signal, not a sole
        # whitelist, because the flat project export has no POS information.
        accept = (is_old or (length_ok and frequency >= args.min_frequency)) and word not in block
        if accept:
            accepted.add(word)
        report.append({
            "word": word,
            "accepted": int(accept),
            "old_rnc": int(is_old),
            "opencorpora": 1,
            "wiktionary": int(wiktionary),
            "frequency": frequency,
            "tags": tags,
            "reject_reason": "accepted" if accept else ("blocklist" if word in block else "short" if not length_ok else "no_frequency"),
        })
    for word in sorted(old - accepted):
        report.append({"word": word, "accepted": 0, "old_rnc": 1, "opencorpora": 0,
                       "wiktionary": int(word in wiki), "frequency": frequencies.get(word, 0),
                       "tags": "", "reject_reason": "blocklist"})

    final = sorted(w for w in accepted if CYRILLIC.fullmatch(w) and (w in old or len(w) >= 4))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(final) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    fields = ["word", "accepted", "old_rnc", "opencorpora", "wiktionary", "frequency", "tags", "reject_reason"]
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(report, key=lambda row: str(row["word"])))
    print(f"old={len(old)} opencorpora={len(oc)} final={len(final)} added={len(set(final)-old)}")
    print(f"new_len4={sum(w not in old and len(w) == 4 for w in final)} new_len5plus={sum(w not in old and len(w) >= 5 for w in final)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
