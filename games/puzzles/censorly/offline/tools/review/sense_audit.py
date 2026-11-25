"""Find Censorly senses that join words of different modern meaning.

A sense (``sense:*`` in ``sense_rules.tsv`` / ``sense_assignments.tsv``)
should hold one modern meaning. A wide one can hide several: the old
``sense:cognize`` joined сознание with познавательный, ``review_success``
joined успех with успеть, and both reached players as complaints.

The signal is the Tikhonov root of each member. Tikhonov segments by
modern word structure, so where the meaning drifted he often fuses the
prefix into the root (со+зна → «созна», у+спех → «успех») or names
another root outright (дёрн in ``sense:hold``). Inside each sense the
members are grouped by Tikhonov root:

``core``
    the largest group, the roots of the sense's Kuznetsova families (ход,
    й, хаж for walking) and their alternants (тяг / тяж / тя, лож / лег).
    Not listed.
``fused``
    a prefix plus a core root (о+держ, со+стяза, у+спех). Often a drifted
    meaning, often fine (вступление); the review decides.
``foreign``
    nothing in common with the core spelling (дерн, пир, ступень). The
    strongest sign of a wrong member, or of a sense joined by etymology only.

Groups of one lemma are listed when ``--min-group 1``; the default 2 shows
the clusters a sense really splits into.

With ``--db`` (under ``with_rds.sh --raw`` for prod, read-only) the CLOSED
complaints in ``CensorlyReviewCase`` that are not rejected and that the
matcher still opens are matched against the senses: a sense both sides
of a complaint share gets it, and senses with complaints rank first. A
complaint on a homograph (три → Трои) lands there too; it is fixed in the
readings, not by a split.

Reviewed groups go to ``lexical/data/sense_audit_reviewed.tsv``
(``sense tikhonov_root verdict note``, verdict ``keep`` when the group
belongs). The audit skips them; ``--all`` shows them again. A group that
was split is not written there: after the split it is no longer in the
sense. Runtime never reads that file.

Usage::

    ../venv/interoves_django/bin/python -m \\
        games.puzzles.censorly.offline.tools.review.sense_audit \\
        --count 40 --out /tmp/sense_audit.tsv
    … sense_audit --sense sense:hold          # one sense in full
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import django

DATA = Path(__file__).resolve().parents[3] / 'lexical' / 'data'
REVIEWED = DATA / 'sense_audit_reviewed.tsv'
REVIEWED_COLUMNS = ('sense', 'tikhonov_root', 'verdict', 'note')

# Longest first, so «пере» wins over «пе».
PREFIXES = tuple(sorted({
    'без', 'бес', 'в', 'во', 'воз', 'вос', 'вз', 'вс', 'вы', 'до', 'за', 'из', 'ис',
    'на', 'над', 'надо', 'недо', 'не', 'низ', 'нис', 'о', 'об', 'обо', 'от', 'ото',
    'пере', 'по', 'под', 'подо', 'пред', 'предо', 'пре', 'при', 'про', 'раз', 'рас',
    'разо', 'роз', 'рос', 'с', 'со', 'су', 'у', 'обез', 'обес', 'ны',
    'объ', 'подъ', 'предъ', 'изъ', 'съ', 'въ', 'взъ', 'отъ', 'разъ', 'надъ',
}, key=len, reverse=True))

# Consonants that alternate inside one Russian root, mapped to one class.
_CLASS = {
    'г': 'G', 'ж': 'G', 'з': 'G', 'дж': 'G',
    'к': 'K', 'ч': 'K', 'ц': 'K',
    'х': 'X', 'ш': 'X', 'с': 'X',
    'д': 'T', 'т': 'T', 'щ': 'T',
    'б': 'B', 'п': 'B',
    'в': 'V', 'ф': 'V',
}
_VOWELS = set('аеёиоуыэюяйьъ')


# A root-final consonant alternates more freely: ход / хож / хожд, крут / круч.
_FINAL = set('дтжчщзсшгкцх')


def skeleton(root: str) -> str:
    """Consonant classes of a root: «тяг», «тяж» and «тя» → «TD», «TD», «T»;
    «хожд» → «XD» like «ход»."""
    out = []
    for char in root.replace('ё', 'е'):
        if char in _VOWELS:
            continue
        out.append(_CLASS.get(char, char))
    final = {_CLASS.get(c, c) for c in _FINAL}
    if len(out) >= 3 and out[-1] in final and out[-2] in final:
        out.pop()  # хожд → хож
    if len(out) >= 2 and out[-1] in final:
        out[-1] = 'D'
    return ''.join(out)


def family_roots(family: str) -> list[str]:
    """Spellings a Kuznetsova family name stands for: «зна(j)» → [«зна»], «ник|ниц» → both."""
    roots = []
    for part in family.split('|'):
        plain = ''.join(c for c in part.split('(')[0] if c.isalpha())
        if plain:
            roots.append(plain)
    return roots


def _alternant(a: str, b: str) -> bool:
    """Same root up to vowel and consonant alternation; one may be truncated."""
    sa, sb = skeleton(a), skeleton(b)
    if not sa or not sb:
        return a[:1] == b[:1]
    short, long_ = sorted((sa, sb), key=len)
    return long_.startswith(short) and (len(short) >= 2 or len(long_) <= 2 or a[:1] == b[:1])


def _strip_prefix(root: str, core: list[str]) -> str:
    """The prefix fused into ``root`` before a core root, or ''."""
    for prefix in PREFIXES:
        rest = root[len(prefix):]
        if root.startswith(prefix) and len(rest) >= 2:
            if any(_alternant(rest, c) for c in core):
                return prefix
            # Two fused prefixes: «исступл», «предрасполаг».
            for inner in PREFIXES:
                tail = rest[len(inner):]
                if rest.startswith(inner) and len(tail) >= 2 and any(_alternant(tail, c) for c in core):
                    return prefix + '+' + inner
    return ''


@dataclass
class Group:
    root: str
    members: list[str]
    kind: str = 'core'
    prefix: str = ''
    freq: int = 0


@dataclass
class SenseReport:
    sense: str
    families: list[str]
    size: int
    groups: list[Group]
    complaints: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> list[Group]:
        return [g for g in self.groups if g.kind != 'core']

    @property
    def score(self) -> float:
        weight = {'foreign': 3.0, 'fused': 1.0}
        drift = sum(weight[g.kind] * (1 + len(g.members)) * (1 + g.freq) ** 0.25 for g in self.flagged)
        return drift + 1000 * len(self.complaints)


def _frequencies() -> dict[str, int]:
    with gzip.open(DATA / 'corpus_lemma_freq.tsv.gz', 'rt', encoding='utf-8') as stream:
        next(stream)
        return {
            lemma: int(tokens)
            for lemma, tokens, *_rest in (line.rstrip('\n').split('\t') for line in stream)
        }


def _families() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    with open(DATA / 'dictionaries' / 'lemmas_to_roots.tsv', encoding='utf-8') as stream:
        for row in csv.reader(stream, delimiter='\t'):
            if len(row) >= 2 and '+' not in row[1]:
                found[row[0]] = [row[1]]
    return found


def load_reviewed() -> dict[tuple[str, str], str]:
    if not REVIEWED.exists():
        return {}
    with open(REVIEWED, encoding='utf-8') as stream:
        rows = csv.DictReader(stream, delimiter='\t')
        return {(row['sense'], row['tikhonov_root']): row['verdict'] for row in rows}


def classify(
    members: list[str], roots_of, freq: dict[str, int], family_core: list[str] = (),
) -> list[Group]:
    """Group ``members`` by Tikhonov root and mark each group core / fused / foreign."""
    by_root: dict[str, list[str]] = defaultdict(list)
    for lemma in members:
        roots = roots_of(lemma)
        # A compound keeps its whole root list as one key.
        by_root['+'.join(roots) if roots else '?'].append(lemma)
    unknown = by_root.pop('?', [])
    groups = [
        Group(root, sorted(lemmas, key=lambda w: -freq.get(w, 0)), freq=sum(freq.get(w, 0) for w in lemmas))
        for root, lemmas in by_root.items()
    ]
    if not groups:
        return [Group('?', unknown, kind='core')] if unknown else []
    groups.sort(key=lambda g: (
        -len(g.members), not any(_alternant(g.root, f) for f in family_core), -g.freq, g.root,
    ))
    # The largest group always counts: a sense may be one prefixed stem
    # throughout (состав, представ) with only a stray lemma on the bare root.
    core = list(dict.fromkeys([groups[0].root, *family_core]))
    # Grow the core with alternants of any core root (тяг → тяж → тя).
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.root not in core and '+' not in group.root and any(_alternant(group.root, c) for c in core):
                core.append(group.root)
                changed = True
    for group in groups:
        if group.root in core:
            continue
        if '+' in group.root:
            parts = group.root.split('+')
            group.kind = 'core' if any(_alternant(p, c) for p in parts for c in core) else 'foreign'
            continue
        prefix = _strip_prefix(group.root, core)
        group.kind, group.prefix = ('fused', prefix) if prefix else ('foreign', '')
    groups = _merge_flagged(groups)
    if unknown:
        groups.append(Group('?', sorted(unknown, key=lambda w: -freq.get(w, 0)), kind='core'))
    return groups


def _merge_flagged(groups: list[Group]) -> list[Group]:
    """Fold a flagged group into an earlier flagged alternant (уведомл → уведом)."""
    kept: list[Group] = []
    for group in groups:
        host = next((
            k for k in kept
            if group.kind != 'core' and k.kind != 'core' and '+' not in group.root
            and _alternant(group.root, k.root)
        ), None)
        if host is None:
            kept.append(group)
            continue
        host.root = f'{host.root}/{group.root}'
        host.members += group.members
        host.freq += group.freq
    return kept


def _complaints(sense_of) -> dict[str, list[str]]:
    from games.models.content import CensorlyReviewCase
    from games.puzzles.censorly.lexical.match import matching_lemmas
    from games.puzzles.censorly.lexical.semantics import readings_of
    from games.puzzles.censorly.text.tokenize import tokenize_text

    def senses(word: str) -> set[str]:
        return {s for item in readings_of(word) for s in sense_of.get(item.lemma, ())}

    found: dict[str, list[str]] = defaultdict(list)
    cases = CensorlyReviewCase.objects.filter(expected=CensorlyReviewCase.EXPECTED_CLOSED)
    for case in cases.exclude(status=CensorlyReviewCase.STATUS_REJECTED):
        # Only complaints the game still opens: the fixed ones are history.
        # Tokenized like an article, so target_readings filters apply.
        if not matching_lemmas(tokenize_text(case.target), case.guess):
            continue
        for sense in senses(case.guess) & senses(case.target):
            found[sense].append(f'{case.guess}→{case.target}')
    return found


def audit(*, min_size: int, min_group: int, show_all: bool, use_db: bool) -> list[SenseReport]:
    from games.puzzles.censorly.lexical.sources.tikhonov import TIKHONOV
    from games.puzzles.censorly.lexical.splits import ASSIGNMENTS

    freq = _frequencies()
    families = _families()
    reviewed = {} if show_all else load_reviewed()
    complaints = _complaints(ASSIGNMENTS) if use_db else {}
    members: dict[str, list[str]] = defaultdict(list)
    for lemma, senses in ASSIGNMENTS.items():
        for sense in senses:
            members[sense].append(lemma)
    reports = []
    for sense, lemmas in members.items():
        if len(lemmas) < min_size and sense not in complaints:
            continue
        fams = Counter(f for lemma in lemmas for f in families.get(lemma, ()))
        family_core = [
            root for f, n in fams.items() if n >= 2 or len(fams) == 1 for root in family_roots(f)
        ]
        groups = classify(lemmas, TIKHONOV.roots_of, freq, family_core)
        for group in groups:
            if group.kind != 'core' and (
                len(group.members) < min_group or (sense, group.root) in reviewed
            ):
                group.kind = 'core'
        report = SenseReport(
            sense, [f for f, _n in fams.most_common(3)], len(lemmas), groups,
            complaints.get(sense, []),
        )
        if report.flagged or report.complaints:
            reports.append(report)
    reports.sort(key=lambda r: (-r.score, r.sense))
    return reports


def _members(group: Group, limit: int) -> str:
    shown = ' '.join(group.members[:limit])
    return shown + (f' …+{len(group.members) - limit}' if len(group.members) > limit else '')


def print_report(report: SenseReport, *, limit: int, stream=sys.stdout) -> None:
    head = f'{report.sense}  size={report.size}  families={",".join(report.families)}  score={report.score:.0f}'
    print(head, file=stream)
    for complaint in report.complaints:
        print(f'  complaint: {complaint}', file=stream)
    for group in report.groups:
        mark = {'core': ' ', 'fused': '~', 'foreign': '!'}[group.kind]
        prefix = f' [{group.prefix}+]' if group.prefix else ''
        print(f'  {mark} {group.root}{prefix} ({len(group.members)}): {_members(group, limit)}', file=stream)
    print(file=stream)


def write_tsv(reports: list[SenseReport], path: Path) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow((
            'rank', 'score', 'sense', 'families', 'size', 'kind', 'tikhonov_root',
            'prefix', 'group_size', 'corpus_tokens', 'members', 'core_sample', 'complaints',
        ))
        for rank, report in enumerate(reports, 1):
            core = [w for g in report.groups if g.kind == 'core' for w in g.members]
            for group in report.flagged:
                writer.writerow((
                    rank, f'{report.score:.1f}', report.sense, ','.join(report.families),
                    report.size, group.kind, group.root, group.prefix, len(group.members),
                    group.freq, ' '.join(group.members), ' '.join(core[:8]),
                    '; '.join(report.complaints),
                ))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--count', type=int, default=30, help='senses to print (default 30)')
    parser.add_argument('--min-size', type=int, default=6, help='skip smaller senses (default 6)')
    parser.add_argument('--min-group', type=int, default=2, help='smallest flagged group (default 2)')
    parser.add_argument('--limit', type=int, default=10, help='members shown per group')
    parser.add_argument('--sense', action='append', default=[], help='show only these senses, in full')
    parser.add_argument('--all', action='store_true', help='include groups marked keep')
    parser.add_argument('--db', action='store_true', help='rank by CLOSED complaints from the database')
    parser.add_argument('--out', type=Path, help='TSV with one row per flagged group')
    args = parser.parse_args(argv)

    if args.sense:
        args.min_size, args.min_group, args.count, args.limit = 1, 1, len(args.sense), 10_000
    reports = audit(
        min_size=args.min_size, min_group=args.min_group, show_all=args.all, use_db=args.db,
    )
    if args.sense:
        wanted = {s if s.startswith('sense:') else f'sense:{s}' for s in args.sense}
        reports = [r for r in reports if r.sense in wanted]
    for report in reports[:args.count]:
        print_report(report, limit=args.limit)
    if args.out:
        write_tsv(reports, args.out)
    kinds = Counter(g.kind for r in reports for g in r.flagged)
    print(
        f'SUMMARY senses={len(reports)} foreign_groups={kinds["foreign"]} '
        f'fused_groups={kinds["fused"]} with_complaints={sum(1 for r in reports if r.complaints)}'
    )


if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    django.setup()
    main()
