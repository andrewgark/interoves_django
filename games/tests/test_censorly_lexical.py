"""Precision tests for the censorly lexical resolver.

Cognates are not wired into play. These tests call the resolver directly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from django.test import SimpleTestCase

from games.censorly.roots import opens_with
from games.censorly.lexical.core import fold
from games.censorly.lexical.dispatcher import backend_name
from games.censorly.lexical.generic_backend import CAPABILITIES as GENERIC_CAPS
from games.censorly.lexical.relations.graph import edge_count, neighbors
from games.censorly.lexical.resolver import (
    describe_token,
    open_prepared,
    open_targets,
    prepare_target,
    related,
    relation_kind,
)
from games.censorly.lexical.russian import CAPABILITIES as RUSSIAN_CAPS
from games.censorly.lexical.russian.decisions import REJECT
from games.censorly.lexical.russian.context import inflection_matches, occurrence_of
from games.censorly.lexical.russian.morphology import cognate_lemmas
from games.censorly.lexical.match import matching_lemmas
from games.censorly.lexical.rootbank import structures_of
from games.censorly.normalize import lemma_of, normalize_surface
from games.censorly.redact import lemmas_matching_guess
from games.censorly.tokenize import build_puzzle_payload


class ReviewedSemanticSplitTests(SimpleTestCase):
    """Keep the meaning splits reviewed in the root-family audit separate."""

    def test_dialogue_split_families_have_distinct_game_roots(self):
        pairs = (
            ('строительство', 'устройство'),
            ('видеть', 'ненависть'),
            ('найти', 'идти'),
            ('найти', 'произойти'),
            ('жизнь', 'животное'),
            ('горький', 'гореть'),
            ('ведать', 'ведомство'),
            ('ряд', 'подрядчик'),
            ('течь', 'втачивать'),
            ('нести', 'отношение'),
            ('год', 'погода'),
            ('мать', 'наматывать'),
            ('держать', 'дёргать'),
            ('дело', 'действие'),
            ('поддержка', 'задержанный'),
        )
        for left, right in pairs:
            left_roots = set(structures_of(left))
            right_roots = set(structures_of(right))
            self.assertTrue(left_roots, f'no roots for {left}')
            self.assertTrue(right_roots, f'no roots for {right}')
            self.assertTrue(left_roots.isdisjoint(right_roots), f'{left} / {right}')

    def test_part_and_frequency_words_stay_separate(self):
        # части is an inflected form of часть; часто is a frequency adverb.
        self.assertFalse(opens_with('части', 'часто'))
        self.assertFalse(opens_with('часто', 'части'))

    def test_west_fate_and_small_boy_senses_are_separate(self):
        west = ('sense:review_west',)
        falling = ('sense:fall',)
        court = ('sense:court',)
        fate = ('sense:fate',)
        small = ('sense:review_small',)
        boy = ('sense:review_boy',)
        self.assertEqual(structures_of('запад'), (west,))
        self.assertEqual(structures_of('западный'), (west,))
        self.assertNotEqual(west, falling)
        self.assertEqual(structures_of('суд'), (court,))
        self.assertEqual(structures_of('судьба'), (fate,))
        self.assertEqual(structures_of('суженый'), (fate,))
        self.assertEqual(structures_of('малый'), (small,))
        self.assertEqual(structures_of('маленький'), (small,))
        self.assertEqual(structures_of('мальчик'), (boy,))
        self.assertEqual(structures_of('малыш'), (boy,))

    def test_ending_law_and_lace_rotation_senses_are_separate(self):
        law = ('sense:review_law',)
        end = ('sense:review_end',)
        rotation = ('sense:review_rotation',)
        lace = ('sense:review_lace',)
        circle = ('sense:review_circle',)
        self.assertEqual(structures_of('закон'), (law,))
        for lemma in ('закончить', 'законченный', 'заканчиваться'):
            self.assertEqual(structures_of(lemma), (end,), lemma)
        self.assertEqual(structures_of('круг'), (circle,))
        self.assertEqual(structures_of('кружить'), (rotation,))
        self.assertEqual(structures_of('кружево'), (lace,))
        self.assertEqual(structures_of('кружевной'), (lace,))
        self.assertEqual(structures_of('гора'), (('fam:гор¹',),))
        self.assertEqual(structures_of('нагорный'), (('fam:гор¹',),))
        self.assertEqual(structures_of('угорать'), (('sense:fumes',),))
        self.assertEqual(structures_of('угореть'), (('sense:fumes',),))
        self.assertEqual(structures_of('гореть'), (('sense:burn',),))

    def test_head_fish_and_firebrand_families_are_separate(self):
        self.assertEqual(structures_of('голова'), (('root:head',),))
        self.assertEqual(structures_of('главный'), (('root:chief',),))
        self.assertEqual(structures_of('голавль'), (('sense:fish',),))
        self.assertEqual(structures_of('голавлевый'), (('sense:fish',),))
        self.assertEqual(structures_of('головешка'), (('sense:firebrand',),))
        self.assertEqual(structures_of('головневый'), (('sense:smut',),))
        self.assertEqual(
            set(structures_of('головня')),
            {('sense:firebrand',), ('sense:smut',)},
        )

    def test_key_and_spring_keep_separate_readings(self):
        lock_root = ('sense:key',)
        spring_root = ('sense:key_spring',)
        self.assertEqual(set(structures_of('ключ')), {lock_root, spring_root})
        self.assertEqual(structures_of('ключник'), (lock_root,))

    def test_essence_existence_and_presence_are_separate(self):
        self.assertEqual(structures_of('сущность'), (('sense:essence',),))
        self.assertEqual(structures_of('существовать'), (('sense:existence',),))
        self.assertEqual(structures_of('присутствие'), (('sense:presence',),))
        self.assertEqual(
            set(structures_of('суть')),
            {('sense:essence',), ('sense:existence',)},
        )
        self.assertEqual(
            set(structures_of('существо')),
            {('sense:essence',), ('sense:existence',)},
        )

    def test_ability_aid_extortion_and_seeking_are_separate(self):
        expected = {
            'мочь': 'sense:can_v',
            'помогать': 'sense:assist',
            'вымогать': 'sense:extort',
            'домогаться': 'sense:seek',
            'мощи': 'sense:relic',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), ((root,),))

    def test_white_squirrel_and_protein_words_stay_separate(self):
        expected = {
            'белый': 'sense:white',
            'белка': 'sense:squirrel',
            'белок': 'sense:protein',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), ((root,),))

    def test_know_and_nobility_readings_of_znat_are_separate(self):
        self.assertEqual(
            set(structures_of('знать')),
            {('sense:cognize',), ('sense:nobility',)},
        )
        self.assertEqual(structures_of('знатность'), (('sense:nobility',),))
        self.assertEqual(
            set(structures_of('знатный')),
            {('sense:cognize',), ('sense:nobility',)},
        )

    def test_write_and_urinate_readings_of_pisat_are_separate(self):
        for lemma in ('писать', 'писаться'):
            with self.subTest(lemma=lemma):
                self.assertEqual(
                    set(structures_of(lemma)),
                    {('sense:review_writing',), ('sense:urinate',)},
                )

    def test_light_and_society_readings_of_svet_are_separate(self):
        self.assertEqual(structures_of('свет'), (('sense:light',),))
        self.assertEqual(structures_of('светский'), (('sense:society',),))
        self.assertEqual(structures_of('светить'), (('sense:light',),))

    def test_calling_and_designation_words_are_separate(self):
        self.assertEqual(structures_of('звать'), (('sense:review_calling',),))
        self.assertEqual(structures_of('название'), (('sense:review_designation',),))
        self.assertEqual(
            set(structures_of('звание')),
            {('sense:review_calling',), ('sense:review_title',)},
        )

    def test_chin_family_meanings_are_separate(self):
        expected = {
            'починка': 'sense:review_repair',
            'причина': 'sense:review_cause',
            'сочинение': 'sense:review_compose',
            'вчиняться': 'sense:review_legal_filing',
            'подчинение': 'sense:review_rank_order',
            'чинный': 'sense:review_rank_order',
            'бесчинство': 'sense:review_disorder',
            'начинка': 'sense:review_stuff',
            'начало': 'sense:begin_start',
            'начальник': 'sense:authority',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), ((root,),))
        self.assertTrue(set(structures_of('свет')).isdisjoint(structures_of('светский')))

    def test_counting_reading_and_honor_senses_are_separate(self):
        self.assertTrue(set(structures_of('счет')).isdisjoint(structures_of('читать')))
        self.assertTrue(set(structures_of('расчет')).isdisjoint(structures_of('чтение')))
        self.assertTrue(set(structures_of('почет')).isdisjoint(structures_of('читать')))
        self.assertIn(('sense:count',), structures_of('считать'))
        self.assertIn(('root:read',), structures_of('считать'))
        self.assertIn(('sense:honor',), structures_of('почитать'))
        self.assertIn(('root:read',), structures_of('почитать'))

    def test_kosa_homonyms_keep_separate_readings(self):
        self.assertEqual(
            set(structures_of('коса')),
            {
                ('sense:scythe',),
                ('sense:hair_braid',),
                ('sense:spit_landform',),
            },
        )
        self.assertEqual(
            set(structures_of('косить')),
            {('sense:scythe',), ('sense:oblique',)},
        )
        self.assertTrue(set(structures_of('косарь')).isdisjoint(structures_of('косичка')))
        self.assertTrue(set(structures_of('косой')).isdisjoint(structures_of('косичка')))

POSITIVES = (
    ('вместе', 'место'),
    ('он', 'она'),
    ('он', 'они'),
    ('он', 'оно'),
    ('она', 'они'),
    ('она', 'оно'),
    ('они', 'оно'),
    ('опыление', 'пыль'),
    ('вскоре', 'скорость'),
    ('мёд', 'медовый'),
    ('мёд', 'медоносный'),
    ('вводить', 'вывести'),
    ('откладывать', 'отложить'),
    ('день', 'дневный'),
    ('день', 'дневной'),
    ('большой', 'больший'),
    ('один', 'единственный'),
    ('пчела', 'пчелиный'),
    ('пчела', 'пчеловод'),
    ('бежать', 'бег'),
    ('конец', 'кончать'),
    ('нога', 'ножка'),
    ('пять', 'пятый'),
    ('новый', 'вновь'),
)

NEGATIVES = (
    ('воздух', 'дышать'),
    ('достаточный', 'состав'),
    ('достаточный', 'становиться'),
    ('достаточный', 'представлять'),
    ('достаточный', 'остаться'),
    ('необходимый', 'выходить'),
    ('образ', 'раз'),
    ('образ', 'сразу'),
    ('цвет', 'цветковый'),
    ('цвет', 'цветок'),
    ('цвет', 'цветение'),
    ('цвет', 'цветник'),
    ('один', 'однако'),
    ('один', 'одинаковый'),
    ('личинка', 'отличие'),
    ('строить', 'три'),
    ('строить', 'строительство'),
    ('верхний', 'совершать'),
    ('верхний', 'верхнечелюстной'),
    ('печка', 'обеспечить'),
    ('брачный', 'забраться'),
    ('самый', 'самец'),
    ('самый', 'сам'),
    ('самый', 'самка'),
    ('слово', 'условие'),
    ('год', 'погода'),
    ('запас', 'опасность'),
    ('равный', 'уровень'),
    ('длина', 'длиться'),
    ('друг', 'другой'),
    ('полукольцо', 'поляризовать'),
    ('вода', 'водить'),
    ('вода', 'пчеловод'),
    ('лето', 'лететь'),
    ('семь', 'семья'),
    ('полный', 'половина'),
    ('полный', 'полноценный'),
    ('пара', 'испарять'),
    ('соты', 'сотня'),
    ('белый', 'белок'),
    ('белый', 'белка'),
    ('белый', 'белковый'),
    ('мать', 'матка'),
    ('мать', 'маточник'),
    ('мать', 'маточный'),
    ('вместе', 'местность'),
    ('вместе', 'вместо'),
    ('вместе', 'вместить'),
    ('общий', 'общественный'),
    ('сложный', 'складывать'),
    ('сложный', 'складываться'),
    ('вывести', 'возводить'),
    ('вывести', 'разводить'),
    ('жилка', 'сухожилие'),
    ('ценность', 'полноценный'),
    ('следовательно', 'следовать'),
    ('так', 'такой'),
    ('прибыль', 'быть'),
    ('прибыть', 'быть'),
    ('прибыли', 'быть'),
    ('сыр', 'сырость'),
    ('год', 'годный'),
    ('гора', 'горний'),
    ('среда', 'средний'),
    ('свет', 'светский'),
    ('мир', 'мирской'),
    ('свод', 'сводник'),
    ('мель', 'мельник'),
    ('колос', 'колосник'),
    ('плот', 'плотник'),
    ('погреб', 'погребение'),
    ('устав', 'уставание'),
    ('удар', 'ударение'),
    ('завалить', 'завалять'),
    ('плав', 'плавание'),
    ('лебеда', 'лебединый'),
    ('мара', 'маревый'),
    ('марь', 'маревый'),
    ('страна', 'странный'),
    ('суд', 'судный'),
    ('краса', 'красный'),
    ('крупа', 'крупный'),
    ('плоть', 'плотный'),
    ('чета', 'четный'),
    ('душный', 'душа'),
    ('червовый', 'червь'),
)


class LexicalResolverTests(SimpleTestCase):
    def test_guess_prefers_citation_reading_over_inflected_homonym(self):
        tokens = [
            {'kind': 'content', 'surface': 'усталый', 'lemma': 'усталый'},
            {'kind': 'content', 'surface': 'устав', 'lemma': 'устав'},
            {'kind': 'content', 'surface': 'испарять', 'lemma': 'испарять'},
            {'kind': 'content', 'surface': 'пара', 'lemma': 'пара'},
            {'kind': 'content', 'surface': 'быть', 'lemma': 'быть'},
            {'kind': 'content', 'surface': 'суть', 'lemma': 'суть'},
        ]

        self.assertNotIn('усталый', matching_lemmas(tokens, 'устав'))
        self.assertIn('устав', matching_lemmas(tokens, 'устав'))
        self.assertNotIn('испарять', matching_lemmas(tokens, 'пара'))
        self.assertIn('пара', matching_lemmas(tokens, 'пара'))
        self.assertNotIn('быть', matching_lemmas(tokens, 'суть'))

    def test_guess_without_citation_form_keeps_all_live_readings(self):
        tokens = [
            {'kind': 'content', 'surface': 'прибыть', 'lemma': 'прибыть'},
            {'kind': 'content', 'surface': 'прибыль', 'lemma': 'прибыль'},
        ]
        hits = matching_lemmas(tokens, 'прибыли')
        self.assertIn('прибыть', hits)
        self.assertIn('прибыль', hits)

    def test_reviewed_missing_root_assignments_use_gameplay_matcher(self):
        tokens = [
            {'kind': 'content', 'surface': 'сенатор', 'lemma': 'сенатор'},
            {'kind': 'content', 'surface': 'сенатский', 'lemma': 'сенатский'},
            {'kind': 'content', 'surface': 'иволговые', 'lemma': 'иволговый'},
        ]
        self.assertEqual(
            matching_lemmas(tokens, 'сенат'),
            {'сенатор', 'сенатский'},
        )
        self.assertEqual(matching_lemmas(tokens, 'иволга'), {'иволговый'})

    def test_required_positives_are_symmetric(self):
        for left, right in POSITIVES:
            kind = relation_kind(left, right)
            self.assertTrue(kind, f'{left}/{right} stayed closed')
            self.assertEqual(kind, relation_kind(right, left))

    def test_required_negatives_stay_closed(self):
        for left, right in NEGATIVES:
            self.assertFalse(related(left, right), f'{left}/{right} opened as {relation_kind(left, right)}')
            self.assertFalse(related(right, left))

    def test_inflection_is_not_a_cognate_edge(self):
        self.assertEqual(relation_kind('улей', 'улья'), 'inflection')
        self.assertEqual(relation_kind('улей', 'ульями'), 'inflection')
        self.assertEqual(relation_kind('улья', 'ульями'), 'inflection')
        self.assertEqual(relation_kind('пчела', 'пчёлами'), 'inflection')
        self.assertEqual(relation_kind('прибыли', 'прибыть'), 'inflection')
        self.assertEqual(relation_kind('прибыли', 'прибыль'), 'inflection')

    def test_stress_homographs_do_not_merge(self):
        self.assertEqual(relation_kind('стро\u0301ить', 'строи\u0301ть'), '')
        self.assertEqual(relation_kind('строить', 'три'), '')
        self.assertEqual(relation_kind('стро\u0301ить', 'строительство'), '')
        self.assertEqual(relation_kind('строи\u0301ть', 'строительство'), '')

    def test_edges_are_not_transitive(self):
        self.assertTrue(related('вместе', 'место'))
        self.assertFalse(related('вместе', 'местность'))

    def test_arbitrary_unicode_does_not_crash(self):
        tokens = ('пчела', 'прибыли', 'строить', 'Apis', 'mellifera', 'DNA', 'α', 'Hox2', 'hello-world', 'mixed123')
        for token in tokens:
            described = describe_token(token, article_language='ru')
            self.assertIn(described['backend'], ('russian', 'generic'))
            self.assertTrue(described['normalized'])
        for token in tokens:
            if fold(token).isascii() or any('a' <= ch.lower() <= 'z' or ch == 'α' for ch in token):
                self.assertEqual(backend_name(token, article_language='ru'), 'generic')
        self.assertEqual(backend_name('пчела', article_language='ru'), 'russian')
        self.assertEqual(describe_token('прибыли')['lemmas'], 'прибыль,прибыть')
        self.assertFalse(related('прибыли', 'быть'))
        opened = open_targets('Apis', ['Apis', 'apis', 'пчела', 'DNA', 'mellifera'])
        self.assertEqual(opened, ['Apis', 'apis'])
        self.assertEqual(open_targets('фруктяка', ['пчела', 'мёд']), [])
        self.assertEqual(open_targets('xyzzy', ['xyzzy', 'пчела']), ['xyzzy'])

    def test_prepared_index_matches_direct_open(self):
        targets = ['пчёлами', 'пчелиный', 'Apis', 'мёд', 'улья']
        prepared = [prepare_target(item) for item in targets]
        self.assertEqual(
            open_prepared('пчела', prepared),
            open_targets('пчела', targets),
        )

    def test_capabilities(self):
        self.assertEqual(GENERIC_CAPS, frozenset({'exact'}))
        self.assertEqual(RUSSIAN_CAPS, frozenset({'exact', 'inflection', 'derivation'}))

    def test_gameplay_does_not_open_cognates(self):
        payload = build_puzzle_payload(wiki_title='Пчела', body_text='Пчела живёт в улье. Apis mellifera.')
        hits = lemmas_matching_guess(payload, lemma_of('пчелиный'), normalize_surface('пчелиный'))
        self.assertNotIn('пчела', hits)

    def test_non_russian_cyrillic_falls_back(self):
        for token in ('мова', 'сонце', 'що', 'геть', 'місто', 'від'):
            self.assertEqual(backend_name(token, article_language='ru'), 'generic', token)
        self.assertEqual(backend_name('ДНК', article_language='ru'), 'russian')
        self.assertEqual(cognate_lemmas('Лев'), frozenset())
        self.assertEqual(cognate_lemmas('Орел'), frozenset())
        self.assertEqual(cognate_lemmas('Попов'), frozenset())
        self.assertEqual(cognate_lemmas('улей'), frozenset({'улей'}))
        self.assertFalse(related('Попов', 'поп'))
        self.assertFalse(related('Белов', 'белый'))

    def test_protein_plural_rejects_the_squirrel_citation(self):
        phrase = 'Это молочко содержит особые белки, отвечающие за развитие.'
        target = occurrence_of(phrase, 'белки')
        self.assertIsNotNone(target)
        self.assertEqual(target.lemmas, frozenset({'белок'}))
        self.assertFalse(target.conservative)
        self.assertTrue(inflection_matches('белок', target))
        self.assertFalse(inflection_matches('белка', target))
        self.assertEqual(relation_kind('белка', 'белки'), '')
        self.assertEqual(relation_kind('белок', 'белки'), '')

    def test_unresolved_homograph_stays_closed(self):
        for phrase in ('Белки содержатся в пище.', 'Белки бегают по деревьям.'):
            target = occurrence_of(phrase, 'Белки')
            self.assertTrue(target.conservative, phrase)
            self.assertFalse(inflection_matches('белка', target))
            self.assertFalse(inflection_matches('белок', target))

    def test_arrival_and_profit_use_different_identities(self):
        arrived = occurrence_of('Они прибыли из Азии.', 'прибыли')
        self.assertEqual(arrived.lemmas, frozenset({'прибыть'}))
        self.assertTrue(inflection_matches('прибыть', arrived))
        self.assertFalse(inflection_matches('прибыль', arrived))
        profit = occurrence_of('Компания получила большие прибыли.', 'прибыли')
        self.assertEqual(profit.lemmas, frozenset({'прибыль'}))
        self.assertTrue(inflection_matches('прибыль', profit))
        self.assertFalse(inflection_matches('прибыть', profit))
        grew = occurrence_of('Прибыли компании выросли.', 'Прибыли')
        self.assertEqual(grew.lemmas, frozenset({'прибыль'}))
        self.assertFalse(inflection_matches('прибыть', grew))
        bare = occurrence_of('прибыли', 'прибыли')
        self.assertTrue(bare.conservative)
        self.assertFalse(inflection_matches('прибыть', bare))
        self.assertFalse(inflection_matches('прибыль', bare))

    def test_same_surface_changes_identity_with_context(self):
        protein = occurrence_of('Идёт синтез белка.', 'белка')
        squirrel = occurrence_of('В сад пришла белка.', 'белка')
        self.assertEqual(protein.lemmas, frozenset({'белок'}))
        self.assertEqual(squirrel.lemmas, frozenset({'белка'}))
        self.assertTrue(inflection_matches('белок', protein))
        self.assertFalse(inflection_matches('белка', protein))
        self.assertTrue(inflection_matches('белка', squirrel))
        self.assertFalse(inflection_matches('белок', squirrel))

    def test_proper_noun_does_not_inherit_the_common_noun(self):
        city = occurrence_of('Я вижу Орла на карте.', 'Орла')
        bird = occurrence_of('Я вижу орла в небе.', 'орла')
        self.assertNotEqual(city.lexemes, bird.lexemes)
        self.assertTrue(inflection_matches('Орёл', city))
        self.assertFalse(inflection_matches('орёл', city))
        self.assertTrue(inflection_matches('орёл', bird))
        self.assertFalse(inflection_matches('Орёл', bird))
        name = occurrence_of('Лев вышел из дома.', 'Лев')
        animal = occurrence_of('Голодный лев вышел из дома.', 'лев')
        self.assertNotEqual(name.lexemes, animal.lexemes)
        self.assertTrue(inflection_matches('Лев', name))
        self.assertFalse(inflection_matches('лев', name))
        self.assertTrue(inflection_matches('лев', animal))
        self.assertFalse(inflection_matches('Лев', animal))
        hive = occurrence_of('Стенки улья тёмные.', 'улья')
        self.assertEqual(hive.lemmas, frozenset({'улей'}))
        self.assertTrue(inflection_matches('улей', hive))
        led = occurrence_of('Он начал вести дневник.', 'вести')
        self.assertEqual(led.lemmas, frozenset({'вести'}))
        self.assertFalse(inflection_matches('весть', led))
        main = occurrence_of('Он служит главным входом.', 'главным')
        self.assertIn('главный', main.lemmas)
        self.assertFalse(inflection_matches('главное', main))


_GRAPH = Path(__file__).resolve().parents[1] / 'censorly' / 'lexical' / 'data' / 'ru_graph.tsv.gz'
_META = _GRAPH.with_name('ru_graph.meta.json')
_PROVENANCE = _GRAPH.with_name('ru_graph.provenance.tsv')


class GraphQaTests(SimpleTestCase):
    def test_graph_invariants(self):
        seen = set()
        proofs = set()
        with gzip.open(_GRAPH, 'rt', encoding='utf-8') as handle:
            for line in handle:
                left, right, proof = line.rstrip('\n').split('\t')
                self.assertNotEqual(left, right)
                self.assertLess(left, right)
                key = (left, right)
                self.assertNotIn(key, seen)
                seen.add(key)
                self.assertIn(':v1', proof)
                proofs.add(proof)
                self.assertNotIn(frozenset((left, right)), REJECT)
        self.assertEqual(len(seen), edge_count())
        self.assertLessEqual(max(len(neighbors(lemma)) for pair in seen for lemma in pair), 8)
        self.assertEqual(neighbors('строить'), ())
        provenance = _PROVENANCE.read_text(encoding='utf-8').splitlines()
        self.assertEqual(provenance[0], 'left\tright\tproof\tevidence\tmanual')
        self.assertEqual(len(provenance) - 1, len(seen))
        meta = json.loads(_META.read_text(encoding='utf-8'))
        digest = hashlib.sha256(_GRAPH.read_bytes()).hexdigest()
        self.assertEqual(meta['graph_sha256'], digest)
        self.assertEqual(meta['proof_engine'], 'ru-proofs-2')
        self.assertGreaterEqual(len(proofs), 10)

    def test_compile_is_deterministic(self):
        from games.censorly.lexical.russian.compiler import compile_graph
        lemmas = Path('/tmp/ruroots/lemmas_to_roots.tsv')
        groups = Path('/tmp/ruroots/root_groups.txt')
        tikhonov = Path('/tmp/rumorphs/RuMorphs-Lemmas.txt')
        if not (lemmas.is_file() and groups.is_file() and tikhonov.is_file()):
            self.skipTest('dictionary snapshots are not in /tmp')
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = compile_graph(lemmas, groups, tikhonov, root / 'a.tsv.gz')
            second = compile_graph(lemmas, groups, tikhonov, root / 'b.tsv.gz')
            self.assertEqual(
                gzip.open(first, 'rt', encoding='utf-8').read(),
                gzip.open(second, 'rt', encoding='utf-8').read(),
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(_GRAPH.read_bytes()).hexdigest(),
            )
