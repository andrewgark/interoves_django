"""Root-family inventory and full simplified-resolver fuzz.

Review unit is a dictionary root family, not a pair. The fuzz uses the
same readings, lexemes, grammar, aliases and root multisets as
``semantics.explain``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.prelaunch import (
    OOV_GUESSES,
    UNICODE_GUESSES,
    content_surfaces,
    iter_fixtures,
    walk_dictionary,
)
from games.censorly.lexical.rootbank import _SENSE, _game_id, structures_of
from games.censorly.lexical.russian.compiler import (
    _families,
    _lines,
    _norm,
    _plain,
    _strip_note,
)
from games.censorly.lexical.russian.decisions import REJECT
from games.censorly.lexical.semantics import (
    _ALIAS_GROUPS,
    _GRAMMAR_GROUPS,
    initially_open,
    readings_of,
)
from games.censorly.tokenize import tokenize_text
from games.matcher.norm_matcher import MORPH_ANALYZER

DATA = Path(__file__).resolve().parent / 'data'
_KUZ_LEMMAS = Path(os.environ.get('CENSORLY_KUZ_LEMMAS', '/tmp/ruroots/lemmas_to_roots.tsv'))
_KUZ_GROUPS = Path(os.environ.get('CENSORLY_KUZ_GROUPS', '/tmp/ruroots/root_groups.txt'))
_TIKHONOV = Path(os.environ.get('CENSORLY_TIKHONOV', '/tmp/rumorphs/RuMorphs-Lemmas.txt'))
_PREFIXES = (
    'пере', 'пред', 'при', 'под', 'про', 'раз', 'рас', 'вос', 'воз', 'вз',
    'вы', 'до', 'за', 'из', 'ис', 'на', 'об', 'от', 'по', 'со', 'у',
)


# verdict, reason, proposed subfamilies. Absence is UNREVIEWED, not SAFE.
VERDICTS = {
    'крас¹|краш': (
        'SPLIT',
        'Красота и красный цвет — разные современные семьи. красочный и красно оставлены неразмеченными.',
        'BEAUTY: краса, красивый, красота, украсить; RED: красный, краска, красить; UNRESOLVED: красочный, красно',
    ),
    'слав¹|слов¹|слы': (
        'SPLIT',
        'Слава, слово, условие и сословие разошлись. словно оставлено.',
        'GLORY: слава, славить; WORD: слово, словарь; CONDITION: условие, условный; ESTATE: сословие; UNRESOLVED: словно',
    ),
    'плот²|площ²': (
        'SPLIT',
        'Плоть и плотность — разные современные семьи.',
        'FLESH: плоть, плотский, воплотить; DENSE: плотный, плотность, вплоть',
    ),
    'друг|друж¹': (
        'SPLIT',
        'Друг не открывает другой и вдруг.',
        'FRIEND: друг, дружба, дружить; OTHER: другой; SUDDENLY: вдруг',
    ),
    'лебед|лебяж': (
        'SPLIT',
        'Лебеда — растение, лебедь — птица.',
        'ORACH: лебеда; SWAN: лебедь, лебединый, лебедка',
    ),
    'черв': (
        'SPLIT',
        'Червь не открывает карточную масть и червлёный цвет.',
        'WORM: червь, червяк; HEARTS: червовый, червы; CRIMSON: червленый',
    ),
    'колаш|колос|колош¹': (
        'SPLIT',
        'Колос растения не открывает колосник решётки.',
        'GRAIN: колос, колоситься; GRATE: колосник',
    ),
    'сыр': (
        'SPLIT',
        'Сырник не открывает сырость. Поверхность «сыр» всё ещё открывает сырой: это краткая форма прилагательного.',
        'CHEESE: сыр, сырок, сырник; DAMP: сырой, сырость, сыреть',
    ),
    'ворон¹': (
        'SPLIT',
        'Птица, воронение стали и проворонить разделены.',
        'BIRD: ворон, ворона; BLUING: воронение, воронить; OVERLOOK: проворонить',
    ),
    'мар³': (
        'MULTIPLE_SEMANTIC_READINGS',
        'Марь хранит два альтернативных корня, дымку и растение, а не один составной набор.',
        'HAZE: марево, маревый, марь; PLANT: марь; SPECTER: мара',
    ),
    'пек|печ|пещ': (
        'SPLIT',
        'Печь, печаль, опека, пещера и печень разделены. печься оставлено при печи.',
        'BAKE: печь, печка, пекарь; SADNESS: печаль; CARE: опека, обеспечить; CAVE: пещера; LIVER: печень',
    ),
    'мал⁴|мел|мол¹': (
        'SPLIT',
        'Мел, мелкий, мель, молоть и моль — разные современные семьи.',
        'CHALK: мел, мелок; SMALL: мелкий, мелочь; SHOAL: мель; MILL: молоть, мельник; MOTH: моль',
    ),
    'раж¹|раз¹': (
        'SPLIT',
        'Образ, разить и выражать — разные современные семьи. Отражать имеет оба чтения: удар и отражение.',
        'IMAGE: образ, образование; STRIKE: разить, поражать; EXPRESS: выражать; REFLECT: отражатель; OBJECT: возражать; UGLY: безобразный; WIT: соображать; TILE: изразец',
    ),
    'лик¹|лиц|лич': (
        'SPLIT',
        'Личинка, отличие и отличный вынесены. Лицо, облик и улика оставлены одним кластером «лика».',
        'FACE: лицо, лик, облик; LARVA: личинка; DIFFERENCE: отличие, различный; EXCELLENT: отличный',
    ),
    'бер|бир|бор¹|бр¹': (
        'SPLIT',
        'Брак и брань вынесены из брать.',
        'TAKE: брать, выбрать; MARRIAGE: брак, брачный; ABUSE: брань',
    ),
    'мест¹|мещ¹': (
        'SPLIT',
        'Мещанин вынесен. Вместе и вместить остаются одной семьёй места.',
        'PLACE: место, вместе, вместить; BOURGEOIS: мещанин',
    ),
    'род|рож²|рожд': (
        'SPLIT',
        'Родник вынесен. Род, родить и народ остаются одной семьёй.',
        'KIN: род, родить, народ; SPRING: родник',
    ),
    'хаж|ход|хож|хожд': (
        'SPLIT',
        'Необходимый вынесен. Доход и выходить остаются при ходьбе: это приставки.',
        'WALK: ходить, выходить, доход; NECESSITY: необходимый',
    ),
    'ста(j)|сто(j)': (
        'KEEP_ONE_FAMILY',
        'Стоять, стать и ставить остаются одной игровой семьёй. Устав, усталость, сустав и достаточный не возвращаются.',
        'KEEP: стоять, стать, ставить; PEELED: устав, усталость, сустав, достаточный',
    ),
    'сам': (
        'SPLIT',
        'Самец и самка вынесены. Сам и самый остаются.',
        'SELF: сам, самый, самость; MALE: самец, самка',
    ),
    'жал¹': (
        'SPLIT',
        'Жаль и жалеть остаются. Жальник — отдельный курган.',
        'PITY: жаль, жалеть; BARROW: жальник',
    ),
    'так|тат|тач²': (
        'SPLIT',
        'Стачка вынесена. Так и такой остаются одной семьёй.',
        'DEMONSTRATIVE: так, такой; LABOR STRIKE: стачка',
    ),
    'кус²|куш²': (
        'SPLIT',
        'Вкус, искусство и искус разделены.',
        'TASTE: вкус, кушать; ART: искусство, искусный; TEMPT: искус, искусить',
    ),
    'цве|цвес|цвет|цвеч': (
        'SPLIT',
        'Цветок не открывает бесцветный. Лемма цвет оставлена: это и цвет, и цветение.',
        'BLOOM: цветок, цвести; COLOR: бесцветный, цветной; UNRESOLVED: цвет',
    ),
    'бв|бы': (
        'SAFE_ONE_FAMILY',
        'Прибытие и прибыль — приставочные производные быть. Отдельный смысловой разрез не делался.',
        '',
    ),
    'важ²|важд|вед²|вес²|вод¹|вож¹|вожд': (
        'SAFE_ONE_FAMILY',
        'Водить, вывести, свод и сводник оставлены одной приставочной семьёй вести.',
        '',
    ),
    'мир¹': (
        'SAFE_ONE_FAMILY',
        'Мир — и покой, и свет. Мирской нельзя оторвать без выбора значения одной леммы.',
        '',
    ),
    'прав': (
        'SPLIT',
        'Справка, направление и правительство — разные современные семьи. Править хранит оба чтения: власть и правку.',
        'REFERENCE: справка; DIRECT: направление, отправить; GOVERN: правительство; CORRECT: исправить, правило; TRUTH: правда; LAW: право',
    ),
    'общ': (
        'SAFE_ONE_FAMILY',
        'Общий и общественный — одна современная семья.',
        '',
    ),
    'один|одн': (
        'SAFE_ONE_FAMILY',
        'Один и одинаковый — одна семья.',
        '',
    ),
    'серед|сред': (
        'SAFE_ONE_FAMILY',
        'Среда и средний — одна семья середины.',
        '',
    ),
    'дл|дол²': (
        'SAFE_ONE_FAMILY',
        'Длина и длиться — одна семья.',
        '',
    ),
    'удар': (
        'SAFE_ONE_FAMILY',
        'Ударение оставлено при ударе.',
        '',
    ),
    'рав|ров²': (
        'SAFE_ONE_FAMILY',
        'Уровень оставлен при равном.',
        '',
    ),
    'верх|верш¹': (
        'SAFE_ONE_FAMILY',
        'Совершать оставлено при верхе как приставочное завершение.',
        '',
    ),
    'гад²|гаж': (
        'SAFE_ONE_FAMILY',
        'Гад и гадость в современном языке одна оценочная семья.',
        '',
    ),
    'тр¹': (
        'SAFE_ONE_FAMILY',
        'Семья тр¹ — это тройка. строить открывает три отдельным морфологическим чтением, не смешением семьи.',
        '',
    ),
    'праст|прост|прощ': (
        'SPLIT',
        'Простой, простить, простыня и опростать — разные современные семьи. Это главный источник fan-out.',
        'SIMPLE: простой, упростить; FORGIVE: простить, прощение; SHEET: простыня; EMPTY: опростать',
    ),
    'лаг|лег²|леж|леч²|лог|лож¹': (
        'SPLIT',
        'Полог и пологость вынесены как леммы. Поверхность «полог» всё ещё открывает пологость: это краткая форма пологий. Лежать и вложить остаются одной семьёй.',
        'LAY: лежать, вложить, полагать; CANOPY: полог; SLOPE: пологий, пологость',
    ),
    'рук|руч¹|руш¹': (
        'SPLIT',
        'Обруч вынесен. Рука, ручка и обручение остаются семьёй руки.',
        'HAND: рука, ручка, обручение; HOOP: обруч',
    ),
    'след|слеж': (
        'SAFE_ONE_FAMILY',
        'Следовательно оставлено при следовать: это приставочное следствие.',
        '',
    ),
    'пас²': (
        'SPLIT',
        'Запас, опасность, пастух и спасти разделены.',
        'STOCK: запас; DANGER: опасность; HERD: пасти, пастух; SAVE: спасти, спасение',
    ),
    'граб¹|греб|грес|гроб': (
        'SPLIT',
        'Грести, гроб, погреб и погребение разделены. Сугроб и гребень тоже отдельно.',
        'ROW: грести, гребля; COFFIN: гроб; CELLAR: погреб; BURIAL: погребение; ROB: грабить; COMB: гребень; DRIFT: сугроб',
    ),
    'плач³|плес¹|плет|плот¹|плоч': (
        'SPLIT',
        'Плести, плот и плотник разделены. Отдельных лемм на плес- в этой семье нет. Сплетня, плотина и сплотить тоже вынесены.',
        'WEAVE: плести; RAFT: плот; CARPENTER: плотник; GOSSIP: сплетня; DAM: плотина; UNITE: сплотить; WHIP: плеть',
    ),
}


def main() -> int:
    started = time.perf_counter()
    print('inventory', flush=True)
    families, multi, universe = build_inventory()
    print('corpus', flush=True)
    corpus = load_corpus()
    attach_corpus(families, corpus)
    attach_rejects(families)
    score(families)
    print('fuzz', flush=True)
    fuzz = run_fuzz(corpus, families)
    attach_fanout(families, fuzz)
    print('checks', flush=True)
    corpus['service'] = service_audit(corpus)
    corpus['compounds'] = compound_audit()
    write_tables(families, multi, universe, fuzz, corpus)
    elapsed = time.perf_counter() - started
    print(f'total_seconds {elapsed:.1f}', flush=True)
    return 0


def _sense_ids_value(raw) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(raw)


def _sense_ids(lemma: str) -> tuple[str, ...]:
    return _sense_ids_value(_SENSE.get(lemma))


def build_inventory():
    families_map = _families(_KUZ_GROUPS)
    morph_family = {}
    for morph, family in families_map.items():
        morph_family[morph] = family
    grouped = {}
    lemma_roots = defaultdict(list)
    numbered = set()
    for line in _lines(_KUZ_LEMMAS):
        raw_lemma, raw_root = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        root = _norm(raw_root.strip())
        if not lemma or not root:
            continue
        if any(ch in root for ch in '¹²³⁴⁵⁶⁷⁸⁹⁰'):
            numbered.add(root)
        family = morph_family.get(root, root)
        senses = _sense_ids(lemma)
        games = senses or (_game_id(root, family),)
        for game in games:
            lemma_roots[lemma].append((family, game, root))
        bucket = grouped.setdefault(family, {
            'family': family,
            'alternations': family.split('|'),
            'lemmas': [],
            'games': Counter(),
            'senses': Counter(),
            'corpus_lemmas': set(),
            'corpus_occurrences': 0,
            'rejects': [],
        })
        if lemma not in bucket['lemmas']:
            bucket['lemmas'].append(lemma)
        for game in games:
            bucket['games'][game] += 1
        for sense in senses:
            bucket['senses'][sense] += 1
    multi = []
    repeated = 0
    over2 = 0
    max_roots = 0
    single = 0
    for lemma, structs in _all_structures().items():
        width = max((len(item) for item in structs), default=0)
        max_roots = max(max_roots, width)
        if any(len(item) >= 2 for item in structs):
            multi.append(lemma)
            if any(len(item) != len(set(item)) for item in structs):
                repeated += 1
            if any(len(item) > 2 for item in structs):
                over2 += 1
        elif structs:
            single += 1
    universe = {
        'lemmas_with_root': len(lemma_roots),
        'single_root_lemmas': single,
        'multi_root_lemmas': len(multi),
        'repeated_root_lemmas': repeated,
        'over_two_roots': over2,
        'max_roots': max_roots,
        'numbered_roots': len(numbered),
        'manual_senses': len({item for raw in _SENSE.values() for item in _sense_ids_value(raw)}),
        'manual_sense_lemmas': len(_SENSE),
        'identities': len({game for rows in lemma_roots.values() for _fam, game, _root in rows}),
    }
    return grouped, multi, universe


def _all_structures():
    from games.censorly.lexical.rootbank import _bank
    bank, _seconds = _bank()
    return bank


def load_corpus():
    articles = []
    occ_lemmas = Counter()
    occ_surfaces = Counter()
    rows = []
    for rel, _kind, body in iter_fixtures():
        surfaces = content_surfaces(body)
        articles.append((rel, surfaces))
        for surface in surfaces:
            occ_surfaces[fold(surface)] += 1
            if initially_open(surface):
                continue
            for item in readings_of(surface):
                occ_lemmas[item.lemma] += 1
            rows.append((rel, surface))
    return {
        'articles': articles,
        'lemma_occ': occ_lemmas,
        'rows': rows,
    }


def attach_corpus(families, corpus):
    lemma_family = {}
    for family, bucket in families.items():
        for lemma in bucket['lemmas']:
            lemma_family.setdefault(lemma, set()).add(family)
    for lemma, count in corpus['lemma_occ'].items():
        for family in lemma_family.get(lemma, ()):
            families[family]['corpus_lemmas'].add(lemma)
            families[family]['corpus_occurrences'] += count


def attach_rejects(families):
    lemma_family = defaultdict(set)
    for family, bucket in families.items():
        for lemma in bucket['lemmas']:
            lemma_family[lemma].add(family)
    for pair in REJECT:
        left, right = sorted(pair)
        shared = lemma_family[left] & lemma_family[right]
        for family in shared:
            families[family]['rejects'].append(f'{left}/{right}')


def score(families):
    for bucket in families.values():
        lemmas = bucket['lemmas']
        flags = []
        if len(lemmas) >= 40:
            flags.append('large')
        elif len(lemmas) >= 15:
            flags.append('medium_size')
        if len(bucket['alternations']) >= 4:
            flags.append('many_alternations')
        if bucket['rejects']:
            flags.append('old_reject')
        if len(bucket['senses']) >= 1:
            flags.append('partial_split')
        if len(bucket['games']) >= 3:
            flags.append('many_game_ids')
        prefixes = {prefix for lemma in lemmas for prefix in _PREFIXES if lemma.startswith(prefix)}
        if len(prefixes) >= 6:
            flags.append('many_prefixes')
        endings = Counter(lemma[-3:] for lemma in lemmas if len(lemma) >= 3)
        if len(endings) >= 12 and len(lemmas) >= 15:
            flags.append('many_suffixes')
        if bucket['corpus_occurrences'] >= 20:
            flags.append('corpus_hot')
        elif bucket['corpus_occurrences']:
            flags.append('in_corpus')
        bucket['flags'] = flags
        bucket['risk'] = (
            min(len(lemmas), 180)
            + 40 * len(bucket['rejects'])
            + 25 * len(bucket['senses'])
            + min(bucket['corpus_occurrences'], 80)
            + 15 * len(flags)
        )
        if bucket['senses'] or 'root:' in ''.join(bucket['games']):
            bucket['bucket'] = 'ALREADY_SPLIT'
        elif len(lemmas) <= 8 and not bucket['rejects'] and 'many_alternations' not in flags:
            bucket['bucket'] = 'AUTO_SAFE'
        else:
            bucket['bucket'] = 'REVIEW'
        if bucket['bucket'] == 'REVIEW' and bucket['risk'] >= 80:
            bucket['tier'] = 'high'
        elif bucket['bucket'] == 'REVIEW':
            bucket['tier'] = 'medium'
        else:
            bucket['tier'] = ''
        decision = VERDICTS.get(bucket['family'])
        if decision:
            bucket['verdict'] = decision[0]
            bucket['reason'] = decision[1]
            bucket['proposed'] = decision[2]
        else:
            bucket['verdict'] = 'UNREVIEWED'
            bucket['reason'] = ''
            bucket['proposed'] = ''


def run_fuzz(corpus, families):
    print('walking dictionary', flush=True)
    pymorphy_lemmas, ambiguous = walk_dictionary()
    guesses = set(pymorphy_lemmas)
    guesses.update(ambiguous)
    guesses.update(fold(item) for item in OOV_GUESSES + UNICODE_GUESSES)
    for _rel, surfaces in corpus['articles']:
        guesses.update(fold(item) for item in surfaces)
    for extra in ('строить', 'пара', 'белки', 'прибыли', 'замок', 'лук', 'орла'):
        guesses.add(extra)
    print(f'guesses {len(guesses)}', flush=True)
    index, occs = build_target_index(corpus)
    fanout_occ = []
    fanout_lemmas = []
    fanout_families = []
    ge = Counter()
    root_openings = Counter()
    ambiguous_rows = []
    top = []
    per_article = Counter()
    started = time.perf_counter()
    opened_guesses = 0
    opening_rows = 0
    for nth, guess in enumerate(guesses):
        if nth and nth % 20000 == 0:
            print(f'  {nth}', flush=True)
        if not guess or initially_open(guess):
            continue
        hits, reasons, root_families = lookup(guess, index, occs)
        if not hits:
            continue
        opened_guesses += 1
        lemmas = set()
        articles = set()
        for idx in hits:
            opening_rows += 1
            rel, surface, lemma_set, _family = occs[idx]
            lemmas.update(lemma_set)
            articles.add(rel)
            per_article[rel] += 1
        fanout_occ.append(len(hits))
        fanout_lemmas.append(len(lemmas))
        fanout_families.append(len(root_families))
        for limit in (2, 5, 10, 20, 50):
            if len(lemmas) >= limit:
                ge[limit] += 1
        for family in root_families:
            root_openings[family] += len(hits)
        reads = readings_of(guess)
        root_sets = []
        for item in reads:
            root_sets.append(tuple(structures_of(item.lemma)))
        distinct = {item for item in root_sets if item}
        if len(distinct) > 1 and len(top) < 400:
            ambiguous_rows.append((guess, len(lemmas), len(distinct), reasons))
        if len(lemmas) >= 8:
            top.append((len(lemmas), len(hits), guess, reasons, len(root_families), len(articles)))
    top.sort(reverse=True)
    ambiguous_rows.sort(key=lambda item: -item[1])
    elapsed = time.perf_counter() - started
    return {
        'guesses': len(guesses),
        'pymorphy_lemmas': len(pymorphy_lemmas),
        'ambiguous_forms': len(ambiguous),
        'opened_guesses': opened_guesses,
        'opening_rows': opening_rows,
        'fanout_occ': fanout_occ,
        'fanout_lemmas': fanout_lemmas,
        'fanout_families': fanout_families,
        'ge': ge,
        'root_openings': root_openings,
        'top': top[:100],
        'ambiguous': ambiguous_rows[:80],
        'per_article': per_article,
        'seconds': elapsed,
        'occurrences': len(occs),
    }


def build_target_index(corpus):
    by_lexeme = defaultdict(list)
    by_root = defaultdict(list)
    by_fold = defaultdict(list)
    occs = []
    for rel, surfaces in corpus['articles']:
        for surface in surfaces:
            if initially_open(surface):
                continue
            reads = readings_of(surface)
            lemmas = {item.lemma for item in reads}
            families = set()
            for item in reads:
                for struct in structures_of(item.lemma):
                    if len(struct) == 1:
                        families.add(struct[0])
            idx = len(occs)
            occs.append((rel, surface, lemmas, families))
            by_fold[fold(surface)].append(idx)
            for item in reads:
                by_lexeme[(item.para_id, item.lemma)].append(idx)
            from games.censorly.lexical.semantics import _roots
            for struct in _roots(reads, surface):
                by_root[struct].append(idx)
    return {'lexeme': by_lexeme, 'root': by_root, 'fold': by_fold}, occs


def lookup(guess, index, occs):
    from games.censorly.lexical.semantics import _roots
    hits = set(index['fold'].get(fold(guess), ()))
    reasons = set()
    if hits:
        reasons.add('exact')
    reads = readings_of(guess)
    before = len(hits)
    for item in reads:
        hits.update(index['lexeme'].get((item.para_id, item.lemma), ()))
    if len(hits) > before:
        reasons.add('lexeme')
    before = len(hits)
    families = set()
    for struct in _roots(reads, guess):
        hits.update(index['root'].get(struct, ()))
        if len(struct) == 1:
            families.add(struct[0])
    if len(hits) > before:
        reasons.add('root')
    # Drop the guess's own surface. Exact identity is not a root opening.
    hits = {idx for idx in hits if fold(occs[idx][1]) != fold(guess)}
    return hits, reasons, families


def attach_fanout(families, fuzz):
    for family, bucket in families.items():
        bucket['root_opening_hits'] = 0
    # root_openings is keyed by game id, not dictionary family.
    game_to_family = {}
    for family, bucket in families.items():
        for game in bucket['games']:
            game_to_family.setdefault(game, set()).add(family)
    for game, hits in fuzz['root_openings'].items():
        for family in game_to_family.get(game, ()):
            families[family]['root_opening_hits'] += hits


def service_audit(corpus):
    """Count every word token, including stops that content_surfaces drops."""
    from games.censorly.tokenize import tokenize_text
    contested = Counter()
    opened_anyway = Counter()
    opened = 0
    total = 0
    kept_closed = 0
    for _rel, _kind, body in iter_fixtures():
        for tok in tokenize_text(body):
            if tok.get('kind') not in ('content', 'stop'):
                continue
            surface = tok.get('surface') or ''
            if not surface:
                continue
            total += 1
            reads = readings_of(surface)
            service = [item for item in reads if item.pos in {'PREP', 'CONJ', 'PRCL', 'INTJ'}]
            content = [
                item for item in reads
                if item.pos and item.pos not in {'PREP', 'CONJ', 'PRCL', 'INTJ'}
            ]
            key = fold(surface)
            if initially_open(surface):
                opened += 1
            if service and content:
                contested[key] += 1
                if initially_open(surface):
                    opened_anyway[key] += 1
                else:
                    kept_closed += 1
    return {
        'total_word_tokens': total,
        'opened': opened,
        'contested_surfaces': len(contested),
        'contested_kept_closed': kept_closed,
        'opened_despite_content': len(opened_anyway),
        'top': contested.most_common(100),
        'opened_anyway': opened_anyway.most_common(40),
    }


def compound_audit():
    from games.censorly.lexical.rootbank import _bank
    bank, _seconds = _bank()
    leaks = []
    for lemma, structs in bank.items():
        singles = {item[0] for item in structs if len(item) == 1}
        for item in structs:
            if len(item) >= 2 and singles & set(item):
                leaks.append(lemma)
                break
    return {'leaks': leaks[:20], 'leak_count': len(leaks)}


def write_tables(families, multi, universe, fuzz, corpus):
    ordered = sorted(families.values(), key=lambda item: (-item['risk'], -len(item['lemmas'])))
    buckets = Counter(item['bucket'] for item in ordered)
    tiers = Counter(item['tier'] for item in ordered if item['tier'])
    review_path = DATA / 'root_family_review.tsv'
    risk_path = DATA / 'root_family_risk.tsv'
    header = (
        'root_family\talternations\tlemma_count\tlemmas\tpos_distribution\t'
        'corpus_count\tmax_fanout\trisk_flags\tcurrent_split\tverdict\t'
        'proposed_subfamilies\treason\tbucket\trisk'
    )
    lines = [header]
    risk_lines = ['root_family\trisk\tbucket\ttier\tlemma_count\tcorpus_count\tflags\trejects']
    for bucket in ordered:
        lemmas = bucket['lemmas']
        shown = ','.join(lemmas if len(lemmas) <= 80 else lemmas[:80])
        if len(lemmas) > 80:
            shown += f',…(+{len(lemmas) - 80})'
        splits = ','.join(f'{name}:{count}' for name, count in bucket['senses'].most_common())
        reason = bucket.get('reason', '')
        proposed = bucket.get('proposed') or splits
        verdict = bucket['verdict']
        lines.append('\t'.join([
            bucket['family'].replace('\t', ' '),
            '|'.join(bucket['alternations']),
            str(len(lemmas)),
            shown,
            '',
            str(bucket['corpus_occurrences']),
            str(bucket.get('root_opening_hits', 0)),
            ','.join(bucket['flags']),
            splits,
            verdict,
            proposed,
            reason,
            bucket['bucket'],
            str(bucket['risk']),
        ]))
        risk_lines.append('\t'.join([
            bucket['family'].replace('\t', ' '),
            str(bucket['risk']),
            bucket['bucket'],
            bucket['tier'],
            str(len(lemmas)),
            str(bucket['corpus_occurrences']),
            ','.join(bucket['flags']),
            ';'.join(bucket['rejects']),
        ]))
    review_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    risk_path.write_text('\n'.join(risk_lines) + '\n', encoding='utf-8')
    fuzz_lines = ['rank\tunique_lemmas\toccurrences\tguess\treasons\troot_families\tarticles']
    for rank, row in enumerate(fuzz['top'], 1):
        lemmas, occ, guess, reasons, nfam, nart = row
        fuzz_lines.append(f'{rank}\t{lemmas}\t{occ}\t{guess}\t{",".join(sorted(reasons))}\t{nfam}\t{nart}')
    (DATA / 'full_semantics_fuzz.tsv').write_text('\n'.join(fuzz_lines) + '\n', encoding='utf-8')
    report = render_report(ordered, buckets, tiers, multi, universe, fuzz, corpus)
    (DATA / 'full_semantics_report.txt').write_text(report, encoding='utf-8')
    sys.stdout.write(report)


def render_report(ordered, buckets, tiers, multi, universe, fuzz, corpus):
    occs = fuzz['fanout_occ'] or [0]
    lemmas = fuzz['fanout_lemmas'] or [0]
    def pct(values, q):
        ordered_values = sorted(values)
        idx = min(len(ordered_values) - 1, int(q * (len(ordered_values) - 1)))
        return ordered_values[idx]
    checksums = []
    for path in (_KUZ_LEMMAS, _KUZ_GROUPS, _TIKHONOV):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        checksums.append(f'{path.name} {digest}')
    high = [item for item in ordered if item['tier'] == 'high']
    medium = [item for item in ordered if item['tier'] == 'medium']
    verdicts = Counter(item['verdict'] for item in ordered)
    unclear = [item for item in ordered if item['verdict'] == 'UNCLEAR']
    lines = [
        f'lemmas_with_a_kuz_root {universe["lemmas_with_root"]}',
        f'root_identities_after_split {universe["identities"]}',
        f'dictionary_families {len(ordered)}',
        f'single_root_lemmas {universe["single_root_lemmas"]}',
        f'multi_root_lemmas {universe["multi_root_lemmas"]}',
        f'repeated_root_lemmas {universe["repeated_root_lemmas"]}',
        f'lemmas_with_more_than_two_roots {universe["over_two_roots"]}',
        f'max_roots_in_one_structure {universe["max_roots"]}',
        f'numbered_root_spellings {universe["numbered_roots"]}',
        f'manual_sense_ids {universe["manual_senses"]}',
        f'manual_sense_lemmas {universe["manual_sense_lemmas"]}',
        f'AUTO_SAFE {buckets["AUTO_SAFE"]}',
        f'REVIEW {buckets["REVIEW"]}',
        f'REVIEW_high {tiers["high"]}',
        f'REVIEW_medium {tiers["medium"]}',
        f'ALREADY_SPLIT {buckets["ALREADY_SPLIT"]}',
        f'verdict_SPLIT {verdicts["SPLIT"]}',
        f'verdict_SAFE_ONE_FAMILY {verdicts["SAFE_ONE_FAMILY"]}',
        f'verdict_UNCLEAR {verdicts["UNCLEAR"]}',
        f'verdict_UNREVIEWED {verdicts["UNREVIEWED"]}',
        f'hand_decisions_still_open {len(unclear)}',
        f'high_risk_unresolved {len(unclear)}',
        f'compound_subset_leaks {corpus["compounds"]["leak_count"]}',
        f'service_occurrences_initially_open {corpus["service"]["opened"]}',
        f'service_contested_surfaces {corpus["service"]["contested_surfaces"]}',
        f'service_contested_kept_closed {corpus["service"]["contested_kept_closed"]}',
        f'service_opened_despite_content_surfaces {corpus["service"]["opened_despite_content"]}',
        f'service_word_tokens {corpus["service"]["total_word_tokens"]}',
        f'high_review_lemmas {sum(len(item["lemmas"]) for item in high)}',
        f'medium_review_lemmas {sum(len(item["lemmas"]) for item in medium)}',
        f'fuzz_guesses {fuzz["guesses"]}',
        f'pymorphy_lemmas {fuzz["pymorphy_lemmas"]}',
        f'ambiguous_forms {fuzz["ambiguous_forms"]}',
        f'fuzz_seconds {fuzz["seconds"]:.1f}',
        f'target_occurrences_indexed {fuzz["occurrences"]}',
        f'guesses_with_opening {fuzz["opened_guesses"]}',
        f'opening_rows {fuzz["opening_rows"]}',
        f'max_occurrence_fanout {max(occs)}',
        f'max_lemma_fanout {max(lemmas)}',
        f'p50_lemma_fanout {pct(lemmas, 0.50)}',
        f'p95_lemma_fanout {pct(lemmas, 0.95)}',
        f'p99_lemma_fanout {pct(lemmas, 0.99)}',
        f'fanout_ge_2 {fuzz["ge"][2]}',
        f'fanout_ge_5 {fuzz["ge"][5]}',
        f'fanout_ge_10 {fuzz["ge"][10]}',
        f'fanout_ge_20 {fuzz["ge"][20]}',
        f'fanout_ge_50 {fuzz["ge"][50]}',
        'checksums ' + ' '.join(checksums),
        'gate_families',
    ]
    gate_ids = {
        'ста(j)|сто(j)', 'раж¹|раз¹', 'пас²',
        'плач³|плес¹|плет|плот¹|плоч', 'мар³', 'граб¹|греб|грес|гроб',
    }
    for item in ordered:
        if item['family'] not in gate_ids:
            continue
        branches = ', '.join(
            f'{name}:{count}' for name, count in item['senses'].most_common()
        )
        lines.append(
            f'  {item["verdict"]} n={len(item["lemmas"])} {item["family"]} | {branches or "parent family"}'
        )
    lines.extend([
        'unclear_blockers',
    ])
    for item in unclear:
        lines.append(
            f'  n={len(item["lemmas"])} corpus={item["corpus_occurrences"]} {item["family"]}'
        )
        lines.append(f'    {item.get("reason", "")}')
        lines.append(f'    {item.get("proposed", "")}')
    lines.append('service_contested_top')
    for surface, count in corpus['service']['top'][:30]:
        lines.append(f'  {count} {surface}')
    lines.append('service_opened_despite_content')
    for surface, count in corpus['service']['opened_anyway'][:25]:
        lines.append(f'  {count} {surface}')
    if corpus['compounds']['leaks']:
        lines.append('compound_leak_sample ' + ', '.join(corpus['compounds']['leaks']))
    lines.append('top_review')
    for item in high[:40]:
        sample = ', '.join(item['lemmas'][:18])
        lines.append(
            f'  risk={item["risk"]} n={len(item["lemmas"])} corpus={item["corpus_occurrences"]} '
            f'hits={item.get("root_opening_hits", 0)} {item["family"][:80]} | {sample}'
        )
        if item['rejects']:
            lines.append('    rejects ' + '; '.join(item['rejects'][:8]))
    lines.append('top_guesses')
    for row in fuzz['top'][:25]:
        lines.append(f'  lemmas={row[0]} occ={row[1]} {row[2]} {",".join(sorted(row[3]))} families={row[4]}')
    lines.append('ambiguous_readings')
    for guess, nlem, nroots, reasons in fuzz['ambiguous'][:20]:
        lines.append(f'  {guess} target_lemmas={nlem} root_readings={nroots} {",".join(sorted(reasons))}')
    lines.append('per_article_openings')
    for rel, count in fuzz['per_article'].most_common():
        lines.append(f'  {count} {rel}')
    if unclear:
        lines.append(
            f'verdict ROOT SEMANTICS NEED MORE REVIEW / remaining_blockers {len(unclear)}'
        )
    else:
        lines.append('verdict ROOT REVIEW GATE CLOSED / remaining_blockers 0')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    sys.exit(main())
