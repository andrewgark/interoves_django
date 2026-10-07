"""Offline pre-launch audit of the censorly cognate graph.

Reads local puzzle fixtures, builds targets with the real puzzle pipeline,
and fuzzes guesses against that index. Does not change masks or play.
"""

from __future__ import annotations

import os
import resource
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical.core import fold, script_of
from games.censorly.lexical.dispatcher import RUSSIAN, backend_name
from games.censorly.lexical.relations.graph import edge_count, neighbors, proof_between
from games.censorly.lexical.resolver import open_prepared, prepare_target, relation_kind
from games.censorly.lexical.russian.compiler import _families, _lines, _plain, _strip_note
from games.censorly.lexical.russian.decisions import ACCEPT, REJECT
from games.censorly.lexical.russian.morphology import (
    _proper_shares_lemma,
    cognate_lemmas,
    lexeme_ids,
    pos_of,
    readings,
)
from games.censorly.tokenize import build_puzzle_payload
from games.censorly.wiki import _trim_extract
from games.matcher.norm_matcher import MORPH_ANALYZER

ROOT = Path(__file__).resolve().parents[2] / 'tests' / 'censorly_testdata'
DATA = Path(__file__).resolve().parent / 'data'
KUZ_LEMMAS = Path(os.environ.get('CENSORLY_KUZ_LEMMAS', '/tmp/ruroots/lemmas_to_roots.tsv'))
KUZ_GROUPS = Path(os.environ.get('CENSORLY_KUZ_GROUPS', '/tmp/ruroots/root_groups.txt'))

# Corpus-relevant pairs. Suspicious homonyms are REJECT; the rest were read
# against the fixture text. Frequency is not the reason.
SAFE_PAIRS = {
    frozenset(pair) for pair in (
        ('больший', 'большой'),
        ('ввести', 'вводить'),
        ('вводить', 'вывести'),
        ('вера', 'верный'),
        ('включаться', 'включиться'),
        ('вместе', 'место'),
        ('вновь', 'новый'),
        ('вода', 'водный'),
        ('возвести', 'возводить'),
        ('возникать', 'возникнуть'),
        ('вскоре', 'скорость'),
        ('вступать', 'вступить'),
        ('выделить', 'выделять'),
        ('выполнить', 'выполнять'),
        ('газ', 'газовый'),
        ('глава', 'главный'),
        ('голова', 'головной'),
        ('гора', 'горный'),
        ('город', 'городской'),
        ('грудной', 'грудь'),
        ('дальнейший', 'дальний'),
        ('день', 'дневной'),
        ('день', 'дневный'),
        ('дневной', 'дневный'),
        ('достигать', 'достигнуть'),
        ('единственный', 'один'),
        ('жена', 'женский'),
        ('завершать', 'завершить'),
        ('заселить', 'заселять'),
        ('зима', 'зимний'),
        ('изменить', 'изменять'),
        ('измениться', 'изменяться'),
        ('интерес', 'интересный'),
        ('исключать', 'исключить'),
        ('исследователь', 'исследовательский'),
        ('коренной', 'корень'),
        ('лед', 'ледовый'),
        ('летний', 'лето'),
        ('магнит', 'магнитный'),
        ('май', 'майский'),
        ('материк', 'материковый'),
        ('мед', 'медовый'),
        ('мед', 'медоносный'),
        ('мера', 'мерный'),
        ('местный', 'место'),
        ('море', 'морской'),
        ('накладывать', 'наложить'),
        ('населить', 'населять'),
        ('нога', 'ножка'),
        ('обнаруживать', 'обнаружить'),
        ('обобщать', 'обобщить'),
        ('озерный', 'озеро'),
        ('окружать', 'окружить'),
        ('он', 'она'),
        ('он', 'они'),
        ('он', 'оно'),
        ('она', 'они'),
        ('она', 'оно'),
        ('они', 'оно'),
        ('опыление', 'пыль'),
        ('откладывать', 'отложить'),
        ('очерк', 'очерковый'),
        ('пар', 'парной'),
        ('пара', 'парный'),
        ('плод', 'плодный'),
        ('получать', 'получить'),
        ('получаться', 'получиться'),
        ('поступать', 'поступить'),
        ('привести', 'приводить'),
        ('прием', 'приемник'),
        ('применить', 'применять'),
        ('проба', 'пробный'),
        ('провести', 'проводить'),
        ('продолжать', 'продолжить'),
        ('продолжаться', 'продолжиться'),
        ('проникать', 'проникнуть'),
        ('пчела', 'пчелиный'),
        ('пчела', 'пчеловод'),
        ('пчела', 'пчеловодство'),
        ('пятеро', 'пятый'),
        ('пятый', 'пять'),
        ('разделить', 'разделять'),
        ('решать', 'решить'),
        ('рыба', 'рыбный'),
        ('сахар', 'сахарный'),
        ('сбор', 'сборник'),
        ('сброс', 'сбросовый'),
        ('свобода', 'свободный'),
        ('север', 'северный'),
        ('сезон', 'сезонный'),
        ('складываться', 'сложиться'),
        ('совершать', 'совершить'),
        ('совет', 'советник'),
        ('совет', 'советский'),
        ('солдат', 'солдатский'),
        ('сообщать', 'сообщить'),
        ('союз', 'союзный'),
        ('степенный', 'степень'),
        ('сток', 'стоковый'),
        ('сторона', 'сторонний'),
        ('труд', 'трудный'),
        ('удар', 'ударный'),
        ('уступать', 'уступить'),
        ('характер', 'характерный'),
        ('холод', 'холодный'),
        ('церковный', 'церковь'),
        ('частный', 'часть'),
        ('шестой', 'шесть'),
        ('язык', 'языковый'),
    )
}
REMOVED = {
    frozenset(('страна', 'странный')): (
        'relational_adjective:ный:v1',
        'country vs strange; homonymous -ный, same class as год/годный',
    ),
    frozenset(('марь', 'маревый')): (
        'relational_adjective:евый:v1',
        'orache vs haze; same collision as мара/маревый',
    ),
    frozenset(('суд', 'судный')): (
        'relational_adjective:ный:v1',
        'court adjective vs Baikal ships; суда/судах have no судно reading',
    ),
    frozenset(('краса', 'красный')): (
        'relational_adjective:ный:v1',
        'beauty vs red',
    ),
    frozenset(('крупа', 'крупный')): (
        'relational_adjective:ный:v1',
        'grain vs large',
    ),
    frozenset(('плоть', 'плотный')): (
        'relational_adjective:ный:v1',
        'flesh vs dense',
    ),
    frozenset(('чета', 'четный')): (
        'relational_adjective:ный:v1',
        'married couple vs even number; чет/четный stays',
    ),
    frozenset(('душный', 'душа')): (
        'relational_adjective:ный:v1',
        'stuffy vs soul',
    ),
    frozenset(('червовый', 'червь')): (
        'relational_adjective:овый:v1',
        'card suit vs worm; черва/червовый stays',
    ),
}
SPECIAL_REASON = {
    frozenset(('степенный', 'степень')): 'power-mean adjective beside степень in entropy',
    frozenset(('глава', 'главный')): 'head/chief; school cognate',
    frozenset(('совет', 'советник')): 'council/advisor',
    frozenset(('совет', 'советский')): 'council/Soviet, same derivation',
    frozenset(('труд', 'трудный')): 'labor/difficult, school cognate',
    frozenset(('частный', 'часть')): 'part/partial',
    frozenset(('сторона', 'сторонний')): 'side/external',
    frozenset(('вера', 'верный')): 'faith/faithful; the correct-sense is the same word',
    frozenset(('пар', 'парной')): 'steam/steamy, separate from пара/парный',
    frozenset(('пара', 'парный')): 'pair/paired',
    frozenset(('язык', 'языковый')): 'tongue/language adjective',
    frozenset(('прием', 'приемник')): 'reception/receiver',
    frozenset(('мера', 'мерный')): 'measure/measured',
    frozenset(('плод', 'плодный')): 'fruit/fertile',
    frozenset(('вводить', 'вывести')): 'required regression, prefix pair, not the вод family',
    frozenset(('откладывать', 'отложить')): 'required regression, not сложный/складывать',
    frozenset(('мед', 'медовый')): 'required regression',
    frozenset(('мед', 'медоносный')): 'required regression',
    frozenset(('пчела', 'пчелиный')): 'required regression',
}

OOV_GUESSES = (
    'фывапролд', 'пчелла', 'пчола', 'медоноснейший', 'сверхкальдера',
    'что-нибудь', 'по-русски', 'санкт-петербург', 'ДНК', 'СССР', 'КНР',
    'пчёлка-дичка',
)
UNICODE_GUESSES = (
    'Apis', 'mellifera', 'DNA', 'Hox2', 'α', 'hello-world', 'mixed123',
    'мова', 'сонце', 'місто', 'від', 'що', 'геть',
)
INFLECTION_FORMS = (
    'улей', 'улья', 'ульями', 'прибыли', 'белки', 'Орла', 'орла',
    'строить', 'стро́ить', 'строи́ть',
)
NEGATIVES = (
    ('белый', 'белок'), ('мать', 'матка'), ('вместе', 'местность'),
    ('общий', 'общественный'), ('сложный', 'складывать'),
    ('вывести', 'возводить'), ('воздух', 'дышать'), ('строить', 'три'),
    ('страна', 'странный'), ('марь', 'маревый'), ('суд', 'судный'),
    ('краса', 'красный'), ('крупа', 'крупный'), ('плоть', 'плотный'),
    ('чета', 'четный'), ('душный', 'душа'), ('червовый', 'червь'),
    ('год', 'годный'),
    ('лебеда', 'лебединый'), ('мара', 'маревый'),
)


def _load_dictionary_nodes() -> set[str]:
    from games.censorly.lexical.russian.compiler import _norm

    families = _families(KUZ_GROUPS)
    grouped: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for line in _lines(KUZ_LEMMAS):
        raw_lemma, raw_root = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        root = raw_root.strip()
        if not lemma or not root:
            continue
        norm_root = _norm(root)
        grouped[lemma].add((_plain(root), families.get(norm_root, norm_root)))
    return {lemma for lemma, keys in grouped.items() if len(keys) == 1}


def iter_fixtures():
    for path in sorted(ROOT.rglob('*.txt')):
        rel = path.relative_to(ROOT).as_posix()
        if path.parent.name == 'articles':
            kind = 'article'
            body = path.read_text(encoding='utf-8')
        elif path.parent.name == 'leads':
            kind = 'lead'
            body = path.read_text(encoding='utf-8')
        else:
            kind = 'raw' if path.parent == ROOT else 'pool'
            body, _truncated = _trim_extract(path.read_text(encoding='utf-8'))
        yield rel, kind, body


def content_surfaces(body: str) -> list[str]:
    payload = build_puzzle_payload(wiki_title='', body_text=body)
    return [
        tok.get('surface') or ''
        for tok in payload['body_tokens']
        if tok.get('kind') == 'content' and (tok.get('surface') or '')
    ]


def closure_of(lemmas: frozenset[str]) -> frozenset[str]:
    if not lemmas:
        return frozenset()
    sets = [set(neighbors(lemma)) | {lemma} for lemma in lemmas]
    return frozenset.intersection(*[frozenset(item) for item in sets])


def analyze(surface: str, cache: dict) -> dict:
    key = fold(surface)
    found = cache.get(key)
    if found is not None:
        return found
    name = backend_name(surface, article_language='ru')
    if name == RUSSIAN:
        lemmas = cognate_lemmas(surface)
        lexemes = lexeme_ids(surface)
        live = readings(surface)
    else:
        lemmas = frozenset()
        lexemes = frozenset()
        live = ()
    info = {
        'backend': name,
        'lemmas': lemmas,
        'lexemes': lexemes,
        'closure': closure_of(lemmas),
        'ambiguous': len({(item.para_id, item.lemma) for item in live}) > 1,
        'proper': any(item.proper for item in live),
        'live': len(live),
    }
    cache[key] = info
    return info


def _external_safe() -> set[frozenset[str]]:
    path = DATA / 'external_reviewed.tsv'
    if not path.is_file():
        return set()
    pairs = set()
    for line in path.read_text(encoding='utf-8').splitlines()[1:]:
        left, right, verdict = line.split('\t')[:3]
        if verdict == 'SAFE':
            pairs.add(frozenset((left, right)))
    return pairs


_EXTERNAL_SAFE: set[frozenset[str]] | None = None


def pair_verdict(left: str, right: str) -> tuple[str, str]:
    global _EXTERNAL_SAFE
    if _EXTERNAL_SAFE is None:
        _EXTERNAL_SAFE = _external_safe()
    pair = frozenset((fold(left), fold(right)))
    if pair in REMOVED or pair in REJECT:
        reason = REMOVED.get(pair, ('', 'manual REJECT'))[1]
        if pair in REJECT and pair not in REMOVED:
            reason = 'manual REJECT'
        return 'REJECT', reason
    if pair in ACCEPT:
        return 'SAFE', 'manual accept'
    if pair in SAFE_PAIRS:
        return 'SAFE', SPECIAL_REASON.get(pair) or _default_reason(proof_between(left, right))
    if pair in _EXTERNAL_SAFE:
        return 'SAFE', 'reviewed external cognate'
    return 'UNREVIEWED', ''


def verdict_of_lemmas(lemmas: frozenset[str], target: str) -> tuple[str, str]:
    sources = [lemma for lemma in lemmas if lemma != target]
    if not sources:
        return 'SAFE', 'same lemma'
    parts = [pair_verdict(src, target) for src in sources]
    if any(verdict == 'REJECT' for verdict, _reason in parts):
        return 'REJECT', next(reason for verdict, reason in parts if verdict == 'REJECT')
    if all(verdict == 'SAFE' for verdict, _reason in parts):
        return 'SAFE', parts[0][1]
    return 'UNREVIEWED', ''


def _default_reason(proof: str) -> str:
    if proof.startswith('verbal_aspect'):
        return 'same-verb aspect pair'
    if proof.startswith('manual_accept'):
        return 'closed manual family'
    if proof.startswith('relational_adjective'):
        return 'transparent relational adjective'
    if proof.startswith('lemma_suffix'):
        return 'transparent suffix derivation'
    if proof.startswith('ordinal'):
        return 'ordinal of the same numeral'
    if proof.startswith('adjective_degree'):
        return 'degree of the same adjective'
    if proof.startswith('profession'):
        return 'profession of the same noun'
    if proof.startswith('diminutive'):
        return 'diminutive of the same noun'
    if proof.startswith('place_adverb'):
        return 'closed place pair'
    if proof.startswith('deverbal_noun'):
        return 'deverbal noun of the same verb'
    return proof or 'reviewed pair'


def generic_class(surface: str) -> str:
    folded = fold(surface)
    script = script_of(surface)
    if script == 'Latn':
        return 'latin'
    if script == 'Grek':
        return 'greek'
    if any(ch.isdigit() for ch in folded) and any(ch.isalpha() for ch in folded):
        return 'mixed'
    if any(ch.isdigit() for ch in folded):
        return 'digits'
    if '-' in surface or '‐' in surface:
        return 'hyphenated'
    if script == 'Cyrl':
        return 'cyrillic_oov'
    return 'mixed'


def flags_for(guess: str, target: str, *, external: bool, ambiguous: bool, proper: bool) -> str:
    flags = []
    if external:
        flags.append('external_guess')
    if ambiguous:
        flags.append('ambiguous')
    if proper:
        flags.append('proper')
    if min(len(guess), len(target)) <= 3:
        flags.append('short')
    left, right = pos_of(guess), pos_of(target)
    if left and right and left != right:
        flags.append('pos:' + left + '/' + right)
    proof = proof_between(guess, target)
    if proof.startswith('relational_adjective') or proof.startswith('lemma_suffix'):
        flags.append('homonym_prone_proof')
    return ','.join(flags)


def write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        handle.write('\t'.join(header) + '\n')
        for row in rows:
            handle.write('\t'.join(cell.replace('\t', ' ').replace('\n', ' ') for cell in row) + '\n')


def load_corpus():
    prepared: dict[str, dict] = {}
    articles = []
    occurrences = 0
    unique_surfaces: set[str] = set()
    unique_norms: set[str] = set()
    by_article_lemmas: dict[str, set[str]] = {}
    lemma_articles: dict[str, set[str]] = defaultdict(set)
    lemma_count: Counter[str] = Counter()
    surface_examples: dict[str, set[str]] = defaultdict(set)
    token_rows = []
    for rel, kind, body in iter_fixtures():
        surfaces = content_surfaces(body)
        lemmas_here: set[str] = set()
        for surface in surfaces:
            occurrences += 1
            unique_surfaces.add(surface)
            unique_norms.add(fold(surface))
            if surface not in prepared:
                prepared[surface] = prepare_target(surface, article_language='ru')
            item = prepared[surface]
            token_rows.append((rel, surface))
            for lemma in item['lemmas']:
                lemmas_here.add(lemma)
                lemma_articles[lemma].add(rel)
                lemma_count[lemma] += 1
                if len(surface_examples[lemma]) < 6:
                    surface_examples[lemma].add(surface)
        by_article_lemmas[rel] = lemmas_here
        articles.append({
            'id': rel,
            'kind': kind,
            'tokens': len(surfaces),
            'surfaces': len(set(surfaces)),
        })
    return {
        'prepared': prepared,
        'articles': articles,
        'occurrences': occurrences,
        'unique_surfaces': unique_surfaces,
        'unique_norms': unique_norms,
        'by_article_lemmas': by_article_lemmas,
        'lemma_articles': lemma_articles,
        'lemma_count': lemma_count,
        'surface_examples': surface_examples,
        'token_rows': token_rows,
    }


def corpus_edges(corpus: dict) -> list[dict]:
    lemmas = set(corpus['lemma_articles'])
    seen: set[frozenset[str]] = set()
    rows = []
    for lemma in sorted(lemmas):
        for other in neighbors(lemma):
            if other not in lemmas:
                continue
            pair = frozenset((lemma, other))
            if pair in seen:
                continue
            seen.add(pair)
            left, right = sorted(pair)
            cooccur = sorted(
                corpus['lemma_articles'][left] & corpus['lemma_articles'][right]
            )
            verdict, reason = pair_verdict(left, right)
            rows.append({
                'left': left,
                'right': right,
                'proof': proof_between(left, right),
                'cooccur': cooccur,
                'left_articles': sorted(corpus['lemma_articles'][left]),
                'right_articles': sorted(corpus['lemma_articles'][right]),
                'verdict': verdict,
                'reason': reason,
                'left_surfaces': sorted(corpus['surface_examples'][left]),
                'right_surfaces': sorted(corpus['surface_examples'][right]),
            })
    return rows


def direction_status(edge: dict, cache: dict) -> str:
    """Both gameplay directions, using real resolver reads of the lemmas."""
    notes = []
    for src, dst in ((edge['left'], edge['right']), (edge['right'], edge['left'])):
        kind = relation_kind(src, dst)
        src_info = analyze(src, cache)
        # A citation form can be a different pymorphy lemma (пар → пара).
        if kind.startswith('cognate'):
            notes.append(src + '→' + dst + ':' + kind.split(':', 1)[1])
            continue
        if dst in src_info['closure'] and dst not in src_info['lemmas']:
            notes.append(src + '→' + dst + ':closure')
        else:
            notes.append(src + '→' + dst + ':no')
    return ';'.join(notes)


def main() -> int:
    started = time.perf_counter()
    edges_only = '--edges-only' in sys.argv
    print('loading corpus', flush=True)
    corpus = load_corpus()
    cache: dict = {}
    edges = corpus_edges(corpus)
    print(f'fixtures {len(corpus["articles"])} edges {len(edges)}', flush=True)

    unreviewed = [edge for edge in edges if edge['verdict'] == 'UNREVIEWED']
    rejected_here = [edge for edge in edges if edge['verdict'] == 'REJECT']
    for edge in edges:
        edge['directions'] = direction_status(edge, cache)

    review_rows: list[list[str]] = []
    for edge in edges:
        articles = ','.join(edge['cooccur']) or (
            'union:' + ','.join(sorted(set(edge['left_articles']) | set(edge['right_articles'])))
        )
        for src, dst, src_surfaces, dst_surfaces in (
            (edge['left'], edge['right'], edge['left_surfaces'], edge['right_surfaces']),
            (edge['right'], edge['left'], edge['right_surfaces'], edge['left_surfaces']),
        ):
            external = src not in corpus['lemma_articles']
            review_rows.append([
                src,
                dst,
                src,
                dst,
                edge['proof'],
                articles,
                str(corpus['lemma_count'][dst]),
                flags_for(src, dst, external=external, ambiguous=False, proper=False),
                edge['verdict'],
                edge['reason'] + ' | surfaces ' + ','.join(src_surfaces) + ' → ' + ','.join(dst_surfaces)
                + ' | ' + edge['directions'],
            ])
    for pair, (proof, reason) in sorted(REMOVED.items(), key=lambda item: tuple(sorted(item[0]))):
        left, right = sorted(pair)
        for src, dst in ((left, right), (right, left)):
            review_rows.append([
                src, dst, src, dst, proof, '', '0', 'removed', 'REJECT', reason,
            ])

    blockers = []
    if unreviewed:
        blockers.append(f'unreviewed corpus edges: {len(unreviewed)}')
    if rejected_here:
        blockers.append(
            'REJECT pairs still in the graph: '
            + ', '.join('/'.join(sorted((e["left"], e["right"]))) for e in rejected_here)
        )
    for left, right in NEGATIVES:
        kind = relation_kind(left, right)
        if kind.startswith('cognate') or proof_between(fold(left), fold(right)):
            blockers.append(f'negative still opens: {left}/{right} {kind}')

    # Transitivity: a neighbor of a neighbor is not an opening without a direct edge.
    wedges = 0
    wedge_leaks = 0
    corpus_lemmas = set(corpus['lemma_articles'])
    for left in corpus_lemmas:
        for mid in neighbors(left):
            for right in neighbors(mid):
                if right == left or right < left:
                    continue
                if proof_between(left, right):
                    continue
                wedges += 1
                if wedges > 400:
                    continue
                kind = relation_kind(left, right)
                if kind.startswith('cognate'):
                    wedge_leaks += 1
    if wedge_leaks:
        blockers.append(f'transitive openings: {wedge_leaks}')

    for pair in list(REJECT)[:]:
        if len(pair) != 2:
            continue
        left, right = sorted(pair)
        if proof_between(left, right):
            blockers.append(f'REJECT lost: {left}/{right}')

    if edges_only:
        _write_edges_preview(corpus, edges, review_rows, blockers, started)
        return 1 if blockers else 0

    fuzz_started = time.perf_counter()
    nodes = _load_dictionary_nodes()
    print(f'dictionary nodes {len(nodes)}', flush=True)
    fuzz_rows, fuzz_stats = run_fuzz(corpus, cache, nodes)
    fuzz_seconds = time.perf_counter() - fuzz_started
    print('fuzz done', round(fuzz_seconds, 1), flush=True)

    inflection = inflection_audit(corpus, cache)
    ambiguity = ambiguity_audit(corpus, cache)
    proper = proper_audit(corpus)
    generic = generic_audit(corpus)
    for note in inflection['blockers'] + ambiguity['blockers'] + proper['blockers'] + generic['blockers']:
        blockers.append(note)

    high_risk = [row for row in fuzz_rows if row['high_risk'] and row['verdict'] == 'UNREVIEWED']
    if high_risk:
        blockers.append(f'unreviewed high-risk fuzz rows: {len(high_risk)}')

    for row in fuzz_rows:
        if not row['high_risk'] and row['fanout'] < 2:
            continue
        review_rows.append([
            row['guess'],
            row['target'],
            row['guess_identity'],
            row['target_identity'],
            row['proof'],
            row['articles'],
            str(row['times']),
            row['flags'],
            row['verdict'],
            row['reason'],
        ])

    review_rows.sort(key=lambda row: (
        0 if row[8] == 'UNREVIEWED' else 1 if row[8] == 'REJECT' else 2,
        row[0],
        row[1],
    ))
    write_tsv(
        DATA / 'prelaunch_review.tsv',
        ['guess', 'target', 'guess_identity', 'target_identity', 'proof', 'articles',
         'times_possible', 'risk_flags', 'verdict', 'reason'],
        review_rows,
    )
    write_corpus_table(corpus, edges, fuzz_rows)
    write_fuzz_table(fuzz_rows)
    elapsed = time.perf_counter() - started
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = render_report(
        corpus, edges, fuzz_stats, inflection, ambiguity, proper, generic,
        blockers, elapsed, fuzz_seconds, peak_kb, len(nodes),
    )
    (DATA / 'prelaunch_report.txt').write_text(report, encoding='utf-8')
    print(report)
    return 1 if blockers else 0


def _write_edges_preview(corpus, edges, review_rows, blockers, started) -> None:
    rows = []
    for edge in edges:
        rows.append([
            edge['left'], edge['right'], edge['proof'], edge['verdict'],
            ','.join(edge['cooccur']) or '-',
            ','.join(edge['left_surfaces']),
            ','.join(edge['right_surfaces']),
            edge['directions'],
            edge['reason'],
        ])
    write_tsv(
        DATA / 'prelaunch_edges.tsv',
        ['left', 'right', 'proof', 'verdict', 'cooccur', 'left_surfaces', 'right_surfaces',
         'directions', 'reason'],
        rows,
    )
    write_tsv(
        DATA / 'prelaunch_review.tsv',
        ['guess', 'target', 'guess_identity', 'target_identity', 'proof', 'articles',
         'times_possible', 'risk_flags', 'verdict', 'reason'],
        review_rows,
    )
    print(f'edges preview in {elapsed_note(started)} blockers={len(blockers)}')
    for edge in edges:
        if edge['verdict'] != 'SAFE' or 'no' in edge['directions']:
            print(
                edge['verdict'], edge['left'], edge['right'],
                edge['left_surfaces'], '=>', edge['right_surfaces'],
                edge['directions'],
            )
    for note in blockers:
        print('BLOCK', note)


def elapsed_note(started: float) -> str:
    return f'{time.perf_counter() - started:.1f}s'


def run_fuzz(corpus: dict, cache: dict, nodes: set[str]) -> tuple[list[dict], dict]:
    corpus_lemmas = set(corpus['lemma_articles'])
    touching = set(corpus_lemmas)
    for lemma in list(corpus_lemmas):
        touching.update(neighbors(lemma))
    stats = Counter()
    rows = []
    groups = [('graph_node', lemma) for lemma in nodes]
    print('walking pymorphy', flush=True)
    pymorphy_lemmas, ambiguous_forms = walk_dictionary()
    stats['pymorphy_lemmas'] = len(pymorphy_lemmas)
    stats['ambiguous_forms'] = len(ambiguous_forms)
    outside = [lemma for lemma in pymorphy_lemmas if lemma not in nodes]
    stats['pymorphy_outside_graph'] = len(outside)
    groups.extend(('pymorphy_outside', lemma) for lemma in outside)
    groups.extend(('oov', item) for item in OOV_GUESSES)
    groups.extend(('unicode', item) for item in UNICODE_GUESSES)
    # Ambiguous forms whose lemmas can see the corpus.
    amb_candidates = []
    for word, ids in ambiguous_forms.items():
        nfs = {nf for _para, nf in ids}
        if nfs & touching:
            amb_candidates.append(word)
    stats['ambiguous_candidates'] = len(amb_candidates)
    groups.extend(('ambiguous_form', word) for word in amb_candidates)

    seen_guess = set()
    max_fanout = 0
    for group, guess in groups:
        key = fold(guess)
        if not key or key in seen_guess and group not in ('oov', 'unicode', 'ambiguous_form'):
            if key in seen_guess:
                continue
        seen_guess.add(key)
        stats['guesses'] += 1
        stats['group:' + group] += 1
        info = analyze(guess, cache)
        if group == 'unicode' or info['backend'] != RUSSIAN:
            opened = set()
            if info['backend'] != RUSSIAN:
                stats['generic_guesses'] += 1
                # Invariant: a non-Russian guess has no cognate closure.
                if info['closure'] - info['lemmas']:
                    stats['generic_cognate_leak'] += 1
        else:
            opened = (info['closure'] - info['lemmas']) & corpus_lemmas
        if info['ambiguous'] and opened:
            stats['ambiguous_cognate_openings'] += 1
        elif info['ambiguous']:
            stats['ambiguous_no_cognate'] += 1
        if not opened:
            continue
        stats['guesses_with_opening'] += 1
        fanout = len(opened)
        max_fanout = max(max_fanout, fanout)
        if fanout >= 2:
            stats['fanout_ge_2'] += 1
        if fanout >= 3:
            stats['fanout_ge_3'] += 1
        articles = set()
        for target in opened:
            articles.update(corpus['lemma_articles'][target])
        external = key not in corpus_lemmas and guess not in corpus['lemma_articles']
        if external:
            stats['external_openings'] += 1
        for target in sorted(opened):
            proof = ''
            identities = info['lemmas'] or frozenset({key})
            for src in identities:
                proof = proof_between(src, target) or proof
            verdict, reason = verdict_of_lemmas(identities or frozenset({key}), target)
            linked = all(
                proof_between(src, target) or src == target
                for src in (identities or frozenset({key}))
            )
            if identities and not linked:
                verdict, reason = 'REJECT', 'ambiguous guess opened without every reading linked'
            high_risk = (
                verdict == 'UNREVIEWED'
                or fanout >= 2
                or info['ambiguous']
                or info['proper']
            )
            rows.append({
                'guess': guess,
                'target': target,
                'guess_identity': ','.join(sorted(identities)),
                'target_identity': target,
                'proof': proof,
                'articles': ','.join(sorted(corpus['lemma_articles'][target])),
                'article_count': len(articles),
                'fanout': fanout,
                'times': corpus['lemma_count'][target],
                'flags': flags_for(
                    key, target,
                    external=external,
                    ambiguous=info['ambiguous'],
                    proper=info['proper'],
                ) + (',fanout:' + str(fanout) if fanout >= 2 else ''),
                'verdict': verdict,
                'reason': reason or group,
                'high_risk': high_risk or verdict == 'UNREVIEWED',
                'group': group,
                'opened': ','.join(sorted(opened)),
                'proofs': proof,
            })
    stats['max_fanout'] = max_fanout
    stats['opening_rows'] = len(rows)
    return rows, stats


def walk_dictionary() -> tuple[set[str], dict[str, set[tuple[int, str]]]]:
    normal_forms: set[str] = set()
    ambiguous: dict[str, set[tuple[int, str]]] = {}
    current = ''
    ids: set[tuple[int, str]] = set()
    for word, _tag, normal, para, _idx in MORPH_ANALYZER.dictionary.iter_known_words():
        if word != current:
            if len(ids) > 1:
                ambiguous[current] = ids
            current = word
            ids = set()
        nf = fold(normal)
        normal_forms.add(nf)
        ids.add((int(para), nf))
    if current and len(ids) > 1:
        ambiguous[current] = ids
    return normal_forms, ambiguous


def inflection_audit(corpus: dict, cache: dict) -> dict:
    blockers = []
    notes = []
    corpus_lexemes = set()
    for item in corpus['prepared'].values():
        corpus_lexemes.update(item['lexemes'])
    expected = (
        ('улей', 'улья', 'inflection'),
        ('улей', 'ульями', 'inflection'),
        ('прибыли', 'быть', ''),
        ('белый', 'белок', ''),
        ('строить', 'три', ''),
        ('строить', 'строительство', ''),
    )
    for guess, target, want in expected:
        kind = relation_kind(guess, target)
        ok = kind == want
        if not ok:
            blockers.append(f'inflection check {guess}/{target} got {kind!r} want {want!r}')
        notes.append(f'{guess} → {target}: {kind or "closed"}')
    for form in INFLECTION_FORMS:
        info = analyze(form, cache)
        hits = sorted({lemma for para, lemma in info['lexemes'] if (para, lemma) in corpus_lexemes})
        cognates = sorted((info['closure'] - info['lemmas']) & set(corpus['lemma_articles']))
        notes.append(
            f'{form} lexemes={sorted(info["lexemes"])} corpus_hits={hits} cognates={cognates} '
            f'ambiguous={info["ambiguous"]} proper={info["proper"]}'
        )
        if cognates and form.casefold() in ('строить', 'стро́ить', 'строи́ть'):
            blockers.append(f'{form} cognate-expanded to {cognates}')
    return {'blockers': blockers, 'notes': notes}


def ambiguity_audit(corpus: dict, cache: dict) -> dict:
    blockers = []
    ambiguous_surfaces = [
        surface for surface, item in corpus['prepared'].items()
        if analyze(surface, cache)['ambiguous']
    ]
    cognate_open = []
    inflection_only = 0
    corpus_lemmas = set(corpus['lemma_articles'])
    corpus_lexemes = set()
    for item in corpus['prepared'].values():
        corpus_lexemes.update(item['lexemes'])
    for surface in ambiguous_surfaces:
        info = analyze(surface, cache)
        opened = (info['closure'] - info['lemmas']) & corpus_lemmas
        lex_hits = {lemma for para, lemma in info['lexemes'] if (para, lemma) in corpus_lexemes}
        if opened:
            bad = [
                target for target in opened
                if verdict_of_lemmas(info['lemmas'], target)[0] != 'SAFE'
            ]
            cognate_open.append((surface, sorted(opened), sorted(info['lemmas']), bad))
            if bad:
                blockers.append(f'ambiguous surface {surface} opens unreviewed {bad}')
        elif lex_hits - info['lemmas']:
            inflection_only += 1
    return {
        'blockers': blockers,
        'count': len(ambiguous_surfaces),
        'cognate_open': cognate_open,
        'inflection_only': inflection_only,
    }


def proper_audit(corpus: dict) -> dict:
    blockers = []
    hits = []
    for surface in sorted(corpus['prepared']):
        if script_of(surface) != 'Cyrl':
            continue
        common = False
        named = False
        for parse in MORPH_ANALYZER.parse(fold(surface)):
            if not parse.methods_stack:
                continue
            if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
                continue
            grams = parse.tag.grammemes
            if grams & {'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'}:
                named = True
            elif grams & {'NOUN', 'ADJF', 'VERB', 'INFN'}:
                common = True
        if not (named and common):
            continue
        proper_lemmas = set()
        for parse in MORPH_ANALYZER.parse(fold(surface)):
            if not parse.methods_stack:
                continue
            if type(parse.methods_stack[0][0]).__name__ != 'DictionaryAnalyzer':
                continue
            if parse.tag.grammemes & {'Name', 'Surn', 'Patr', 'Geox', 'Orgn', 'Trad'}:
                proper_lemmas.add(fold(parse.normal_form))
        lemmas = cognate_lemmas(surface)
        # A weak name homograph of a common noun may share the lemma and still
        # lose. That is the common word, not the name inheriting the graph.
        kept_proper = bool(lemmas & proper_lemmas) and _proper_shares_lemma(fold(surface))
        opened = (closure_of(lemmas) - lemmas) if kept_proper else set()
        if kept_proper or not lemmas:
            hits.append((surface, sorted(lemmas), sorted(proper_lemmas), sorted(opened)))
        if kept_proper and lemmas:
            blockers.append(
                f'proper noun {surface} kept a name lemma and opens {sorted(opened) or sorted(lemmas)}'
            )
    return {'blockers': blockers, 'hits': hits}


def generic_audit(corpus: dict) -> dict:
    blockers = []
    classes: Counter[str] = Counter()
    checked = 0
    russian_probe = prepare_target('пчела', article_language='ru')
    for surface, item in corpus['prepared'].items():
        if item['backend'] == RUSSIAN:
            continue
        classes[generic_class(surface)] += 1
        prepared = [item, russian_probe]
        opened = open_prepared(surface, prepared, article_language='ru')
        if surface not in opened:
            blockers.append(f'generic exact miss: {surface}')
        if 'пчела' in opened:
            blockers.append(f'generic guess opened russian: {surface}')
        back = open_prepared('пчела', [item], article_language='ru')
        if back:
            blockers.append(f'russian graph guess opened generic: {surface}')
        other = open_prepared(surface + 'x', [item], article_language='ru')
        if other:
            blockers.append(f'different token opened generic: {surface}')
        checked += 1
    for guess in UNICODE_GUESSES:
        if backend_name(guess, article_language='ru') == RUSSIAN:
            continue
        info_lemmas = cognate_lemmas(guess) if script_of(guess) == 'Cyrl' else frozenset()
        if closure_of(info_lemmas):
            blockers.append(f'unicode guess has cognate closure: {guess}')
    return {'blockers': blockers, 'classes': classes, 'checked': checked}


def write_corpus_table(corpus, edges, fuzz_rows) -> None:
    per = {}
    for article in corpus['articles']:
        per[article['id']] = {
            'kind': article['kind'],
            'targets': article['surfaces'],
            'tokens': article['tokens'],
            'safe': 0,
            'reject': 0,
            'unreviewed': 0,
            'edges': 0,
            'fanout': 0,
            'unexpected': 0,
        }
    for edge in edges:
        arts = edge['cooccur'] or []
        for art in arts:
            slot = per[art]
            slot['edges'] += 1
            key = edge['verdict'].lower()
            if key in slot:
                slot[key] += 1
    for row in fuzz_rows:
        for art in row['articles'].split(','):
            if art in per:
                per[art]['fanout'] = max(per[art]['fanout'], row['fanout'])
    for art, lemmas in corpus['by_article_lemmas'].items():
        for left, right in NEGATIVES:
            a, b = fold(left), fold(right)
            if a in lemmas and b in lemmas and (
                proof_between(a, b) or relation_kind(left, right).startswith('cognate')
            ):
                per[art]['unexpected'] += 1
    rows = []
    for art, slot in per.items():
        rows.append([
            art, slot['kind'], str(slot['tokens']), str(slot['targets']),
            str(slot['edges']), str(slot['safe']), str(slot['reject']),
            str(slot['unreviewed']), str(slot['fanout']), str(slot['unexpected']),
        ])
    write_tsv(
        DATA / 'prelaunch_corpus.tsv',
        ['article', 'kind', 'token_count', 'target_count', 'approved_relations',
         'reviewed_SAFE', 'reviewed_REJECT', 'unreviewed', 'max_fanout', 'unexpected_openings'],
        rows,
    )


def write_fuzz_table(fuzz_rows: list[dict]) -> None:
    best: dict[str, dict] = {}
    for row in fuzz_rows:
        current = best.get(row['guess'])
        if current is None or (row['fanout'], row['article_count']) > (
            current['fanout'], current['article_count'],
        ):
            best[row['guess']] = row
    ordered = sorted(
        best.values(),
        key=lambda row: (
            0 if row['verdict'] == 'UNREVIEWED' else 1,
            -row['fanout'],
            -row['article_count'],
            row['guess'],
        ),
    )
    write_tsv(
        DATA / 'prelaunch_fuzz.tsv',
        ['guess', 'opened_targets', 'article_count', 'target_count', 'proofs',
         'review_status', 'group', 'risk_flags'],
        [[
            row['guess'], row['opened'], str(row['article_count']), str(row['fanout']),
            row['proof'], row['verdict'], row['group'], row['flags'],
        ] for row in ordered],
    )


def render_report(
    corpus, edges, fuzz_stats, inflection, ambiguity, proper, generic,
    blockers, elapsed, fuzz_seconds, peak_kb, node_count,
) -> str:
    prepared = corpus['prepared']
    russian = [s for s, item in prepared.items() if item['backend'] == RUSSIAN]
    generic_s = [s for s, item in prepared.items() if item['backend'] != RUSSIAN]
    lexemes = set()
    lemmas = set()
    graph_nodes = set()
    ambiguous = 0
    oov = 0
    for surface, item in prepared.items():
        lexemes.update(item['lexemes'])
        lemmas.update(item['lemmas'])
        for lemma in item['lemmas']:
            if neighbors(lemma):
                graph_nodes.add(lemma)
        if len(item['lexemes']) > 1 or len(item['lemmas']) > 1:
            ambiguous += 1
        if script_of(surface) == 'Cyrl' and item['backend'] != RUSSIAN:
            oov += 1
    safe = sum(1 for edge in edges if edge['verdict'] == 'SAFE')
    reject = sum(1 for edge in edges if edge['verdict'] == 'REJECT')
    pending = sum(1 for edge in edges if edge['verdict'] == 'UNREVIEWED')
    primary = [a for a in corpus['articles'] if a['kind'] != 'lead']
    lines = [
        f'fixtures {len(corpus["articles"])} primary_bodies {len(primary)} leads {len(corpus["articles"]) - len(primary)}',
        f'token_occurrences {corpus["occurrences"]}',
        f'unique_surfaces {len(corpus["unique_surfaces"])}',
        f'unique_normalized {len(corpus["unique_norms"])}',
        f'unique_lexical_identities {len(lexemes)}',
        f'unique_cognate_lemmas {len(lemmas)}',
        f'unique_graph_nodes_used {len(graph_nodes)}',
        f'russian_surfaces {len(russian)} generic_surfaces {len(generic_s)} '
        f'ambiguous_surfaces {ambiguous} cyrillic_oov_surfaces {oov}',
        f'corpus_edges {len(edges)} SAFE {safe} REJECT {reject} UNREVIEWED {pending}',
        f'removed_pairs {len(REMOVED)} graph_edges {edge_count()} dictionary_nodes {node_count}',
        f'fuzz_seconds {fuzz_seconds:.1f} total_seconds {elapsed:.1f} peak_rss_kb {peak_kb}',
        'fuzz ' + ' '.join(f'{key}={fuzz_stats[key]}' for key in sorted(fuzz_stats)),
        f'ambiguous_corpus_surfaces {ambiguity["count"]} '
        f'cognate_exceptions {len(ambiguity["cognate_open"])} '
        f'inflection_only_extra {ambiguity["inflection_only"]}',
        f'proper_collisions {len(proper["hits"])} generic_checked {generic["checked"]} '
        + ' '.join(f'{k}={v}' for k, v in sorted(generic['classes'].items())),
        'inflection:',
        *('  ' + note for note in inflection['notes']),
        'ambiguous cognate exceptions:',
        *('  ' + repr(item) for item in ambiguity['cognate_open'][:40]),
        'proper samples:',
        *('  ' + repr(item) for item in proper['hits'][:40]),
        'blockers:',
        *(blockers or ['  none']),
        'VERDICT ' + ('PRE-LAUNCH GRAPH NOT QUALIFIED' if blockers else 'READY FOR LOCAL FEATURE TEST'),
    ]
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    sys.exit(main())
