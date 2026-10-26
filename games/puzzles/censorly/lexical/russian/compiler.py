"""Build the approved Russian cognate graph from local dictionaries.

Roots only index candidates inside a proof. Pairs that no proof accepts
are not written. REJECT always wins over a proof and over ACCEPT.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

from games.censorly.lexical.core import fold, stress_signature
from games.censorly.lexical.russian.decisions import ACCEPT, REJECT
from games.censorly.lexical.russian.proofs import PROOF_ENGINE, Lexicon, proof_tag, prove

_SUPERSCRIPTS = str.maketrans('', '', '¹²³⁴⁵⁶⁷⁸⁹⁰')
DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'data' / 'ru_graph.tsv.gz'


def compile_graph(
    kuznetsova_lemmas: Path,
    kuznetsova_groups: Path,
    tikhonov_lemmas: Path,
    dest: Path | None = None,
) -> Path:
    dest = dest or DEFAULT_PATH
    families = _families(kuznetsova_groups)
    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for line in _lines(kuznetsova_lemmas):
        raw_lemma, raw_root = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        root = _norm(raw_root.strip())
        if not lemma or not root:
            continue
        plain = _plain(root)
        family = families.get(root, root)
        grouped[lemma].append((stress_signature(raw_lemma), plain, family))

    roots: dict[str, tuple[str, str]] = {}
    ambiguous = 0
    for lemma, rows in grouped.items():
        keys = {(plain, family) for _sig, plain, family in rows}
        if len(keys) != 1:
            ambiguous += 1
            continue
        roots[lemma] = next(iter(keys))

    segs: dict[str, tuple[str, ...]] = {}
    for line in _lines(tikhonov_lemmas):
        raw_lemma, seg = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        if not lemma or lemma in roots:
            # Compounds absent from Kuznetsova are the ones whose parts we read.
            pass
        parts = []
        for part in seg.split('/'):
            if ':' not in part:
                continue
            morph, kind = part.rsplit(':', 1)
            if kind == 'ROOT':
                morph_n = _plain(morph)
                if morph_n:
                    parts.append(morph_n)
        if len(parts) >= 2:
            segs[lemma] = tuple(parts)

    lex = Lexicon(roots, segs)
    proved = prove(lex)
    edges: dict[frozenset[str], tuple[str, str]] = {}
    blocked = 0
    for pair, (proof, evidence) in proved.items():
        if pair in REJECT:
            blocked += 1
            continue
        edges[pair] = (proof, evidence)
    accepted = 0
    for pair in ACCEPT:
        if pair in REJECT or len(pair) != 2 or pair in edges:
            continue
        edges[pair] = (proof_tag('manual_accept'), 'source=manual')
        accepted += 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(edges, key=lambda item: tuple(sorted(item)))
    # mtime=0 keeps the gzip checksum stable across rebuilds.
    with dest.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            lines = []
            for pair in ordered:
                left, right = sorted(pair)
                lines.append(f'{left}\t{right}\t{edges[pair][0]}\n')
            compressed.write(''.join(lines).encode('utf-8'))
    _write_provenance(dest, ordered, edges)
    _write_stats(
        dest,
        {pair: edges[pair][0] for pair in edges},
        nodes=len(roots),
        ambiguous=ambiguous,
        blocked=blocked,
        manual=accepted,
    )
    _write_meta(
        dest,
        kuznetsova_lemmas=kuznetsova_lemmas,
        kuznetsova_groups=kuznetsova_groups,
        tikhonov_lemmas=tikhonov_lemmas,
    )
    return dest


def _write_provenance(dest: Path, ordered, edges: dict) -> None:
    path = dest.with_name('ru_graph.provenance.tsv')
    with path.open('w', encoding='utf-8') as handle:
        handle.write('left\tright\tproof\tevidence\tmanual\n')
        for pair in ordered:
            left, right = sorted(pair)
            proof, evidence = edges[pair]
            manual = 'accept' if proof.startswith('manual_accept') else ''
            handle.write(f'{left}\t{right}\t{proof}\t{evidence}\t{manual}\n')


def _write_meta(dest: Path, **sources: Path) -> None:
    graph_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
    payload = {
        'proof_engine': PROOF_ENGINE,
        'graph_sha256': graph_hash,
        'build_command': (
            'DJANGO_SETTINGS_MODULE=interoves_django.settings '
            '../venv/interoves_django/bin/python '
            '-m games.censorly.lexical.russian.compiler'
        ),
        'sources': [
            {
                'role': role,
                'filename': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'bytes': path.stat().st_size,
            }
            for role, path in sources.items()
        ],
        'license_note': (
            'Inputs are local snapshots of Kuznetsova root tables '
            '(tabidots/ru_roots) and RuMorphs Tikhonov segmentations. '
            'Both derive from copyrighted dictionaries and are not vendored. '
            'Rebuild only from those exact checksums. The runtime graph is '
            'approved pairs, not the source dictionaries. No network at runtime.'
        ),
    }
    dest.with_name('ru_graph.meta.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def _write_stats(dest: Path, edges, *, nodes: int, ambiguous: int, blocked: int, manual: int) -> None:
    from collections import Counter
    degree: Counter[str] = Counter()
    proofs: Counter[str] = Counter()
    for pair, proof in edges.items():
        proofs[proof] += 1
        for lemma in pair:
            degree[lemma] += 1
    degrees = sorted(degree.values())
    graph_nodes = len(degree)
    def pct(values: list[int], q: float) -> int:
        if not values:
            return 0
        index = min(len(values) - 1, int(q * (len(values) - 1)))
        return values[index]
    lines = [
        f'dictionary_nodes\t{nodes}',
        f'graph_nodes\t{graph_nodes}',
        f'ambiguous_lemmas\t{ambiguous}',
        f'approved_edges\t{len(edges)}',
        f'rejected_overrides\t{blocked}',
        f'manual_accept_added\t{manual}',
        f'average_degree_linked\t{(sum(degrees) / len(degrees) if degrees else 0):.3f}',
        f'average_degree_dictionary\t{(sum(degrees) / nodes if nodes else 0):.3f}',
        f'p50_degree_linked\t{pct(degrees, 0.50)}',
        f'p95_degree_linked\t{pct(degrees, 0.95)}',
        f'max_degree\t{(degrees[-1] if degrees else 0)}',
    ]
    for proof, count in proofs.most_common():
        lines.append(f'proof\t{proof}\t{count}')
    lines.append('hubs')
    for lemma, count in degree.most_common(25):
        lines.append(f'hub\t{count}\t{lemma}')
    stats_path = Path(str(dest).removesuffix('.tsv.gz') + '.stats.txt')
    stats_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _families(path: Path) -> dict[str, str]:
    redirect: dict[str, str] = {}
    morph_to_id: dict[str, str] = {}
    for line in _lines(path):
        if '→' in line:
            left, right = (_norm(part.strip()) for part in line.split('→', 1))
            if left and right:
                redirect[left] = right
            continue
        parts = []
        for part in line.split('/'):
            part_n = _norm(part.strip())
            if part_n and part_n not in parts:
                parts.append(part_n)
        if not parts:
            continue
        family = '|'.join(parts)
        for part in parts:
            morph_to_id[part] = family
    resolved = {}
    for morph, family in morph_to_id.items():
        seen: set[str] = set()
        current = morph
        while current in redirect and current not in seen:
            seen.add(current)
            current = redirect[current]
        resolved[morph] = morph_to_id.get(current, family)
    return resolved


def _lines(path: Path):
    text = path.read_text(encoding='utf-8')
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield line


def _strip_note(text: str) -> str:
    for opener, closer in (('(', ')'), ('[', ']')):
        while opener in text and closer in text:
            start = text.find(opener)
            end = text.find(closer, start)
            if end < 0:
                break
            text = text[:start] + text[end + 1:]
    return text.strip()


def _norm(text: str) -> str:
    folded = unicodedata.normalize('NFC', text or '')
    folded = ''.join(char for char in folded if unicodedata.category(char) != 'Mn')
    return folded.lower().replace('ё', 'е').strip()


def _plain(morph: str) -> str:
    plain = _norm(morph).translate(_SUPERSCRIPTS)
    return plain.replace('(j)', '').replace('(', '').replace(')', '')


if __name__ == '__main__':
    import os

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')
    import django
    django.setup()
    compile_graph(
        Path(os.environ.get('CENSORLY_KUZ_LEMMAS', '/tmp/ruroots/lemmas_to_roots.tsv')),
        Path(os.environ.get('CENSORLY_KUZ_GROUPS', '/tmp/ruroots/root_groups.txt')),
        Path(os.environ.get('CENSORLY_TIKHONOV', '/tmp/rumorphs/RuMorphs-Lemmas.txt')),
    )
