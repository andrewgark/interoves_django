"""Precision tests for the censorly lexical resolver.

Cognates are not wired into play. These tests call the resolver directly.
"""

from __future__ import annotations

import csv
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
from games.censorly.tokenize import build_puzzle_payload, tokenize_text


def _has_sense(lemma, sense):
    return any(
        sense in structure
        and any(not part.startswith('sense:') for part in structure)
        for structure in structures_of(lemma)
    )


def _sense_set(lemma):
    return {
        part
        for structure in structures_of(lemma)
        for part in structure
        if part.startswith('sense:')
    }


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
            ('двойня', 'двойник'),
        )
        for left, right in pairs:
            left_roots = set(structures_of(left))
            right_roots = set(structures_of(right))
            self.assertTrue(left_roots, f'no roots for {left}')
            self.assertTrue(right_roots, f'no roots for {right}')
            self.assertTrue(left_roots.isdisjoint(right_roots), f'{left} / {right}')

    def test_two_family_senses_are_assigned_to_distinct_clusters(self):
        from games.censorly.lexical.semantic_splits import CLUSTERS
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        clusters = {lemma: name for name, lemmas in CLUSTERS for lemma in lemmas}
        family_review = (
            Path(__file__).resolve().parents[1]
            / 'puzzles' / 'censorly' / 'lexical' / 'data' / 'root_family_review.tsv'
        )
        with family_review.open(encoding='utf-8', newline='') as stream:
            family = next(
                row for row in csv.DictReader(stream, delimiter='\t')
                if row['root_family'] == 'дв'
            )
        self.assertTrue(set(family['lemmas'].split(',')) <= clusters.keys())
        self.assertEqual(clusters['двойня'], 'sense:twin_birth')
        self.assertEqual(clusters['двойняшка'], 'sense:twin_birth')
        self.assertEqual(clusters['двойник'], 'sense:lookalike')
        self.assertEqual(clusters['два'], 'sense:two')
        self.assertEqual(clusters['двойной'], 'sense:two')

        twins = structures_of('двойня')
        lookalike = structures_of('двойник')
        self.assertTrue(_has_sense('двойня', 'sense:twin_birth'))
        self.assertTrue(_has_sense('двойник', 'sense:lookalike'))
        self.assertFalse(_relation(_GuessPack('двойня'), _Pack('двойник')))
        self.assertFalse(_relation(_GuessPack('двойник'), _Pack('двойня')))
        self.assertTrue(_relation(_GuessPack('двойня'), _Pack('двойняшка')))
        self.assertTrue(_relation(_GuessPack('двойник'), _Pack('двойниковый')))

    def test_binding_and_contact_senses_are_separate(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        self.assertTrue(_has_sense('привязать', 'sense:binding'))
        self.assertTrue(_has_sense('связываться', 'sense:contact'))
        self.assertFalse(_relation(
            _GuessPack('связывалась'), _Pack('привязаны', article_lemma='привязать'),
        ))
        self.assertFalse(_relation(
            _GuessPack('привязаны'), _Pack('связывалась', article_lemma='связываться'),
        ))
        self.assertTrue(_relation(_GuessPack('вязать'), _Pack('привязать')))
        self.assertTrue(_relation(_GuessPack('связываться'), _Pack('связаться')))
        self.assertTrue(_relation(_GuessPack('вязать'), _Pack('связь')))

    def test_part_and_frequency_words_stay_separate(self):
        # части is an inflected form of часть; часто is a frequency adverb.
        self.assertFalse(opens_with('части', 'часто'))
        self.assertFalse(opens_with('часто', 'части'))

    def test_arabic_digits_open_their_cardinal_words_in_gameplay(self):
        from games.censorly.lexical.match import _GuessPack, _Pack, _relation

        for digit, cardinal in (
            ('0', 'ноль'), ('1', 'один'), ('2', 'два'), ('3', 'три'),
            ('4', 'четыре'), ('5', 'пять'), ('6', 'шесть'), ('7', 'семь'),
            ('8', 'восемь'), ('9', 'девять'), ('10', 'десять'),
        ):
            with self.subTest(digit=digit):
                self.assertTrue(_relation(_GuessPack(digit), _Pack(cardinal)))
                self.assertTrue(_relation(_GuessPack(cardinal), _Pack(digit)))

    def test_west_fate_and_small_boy_senses_are_separate(self):
        for lemma in ('запад', 'западный', 'западник', 'западнический'):
            self.assertTrue(_has_sense(lemma, 'sense:review_west'))
        self.assertTrue(_has_sense('суд', 'sense:court'))
        for lemma in ('судьба', 'суженый'):
            self.assertTrue(_has_sense(lemma, 'sense:fate'))
        for lemma in ('малый', 'маленький'):
            self.assertTrue(_has_sense(lemma, 'sense:review_small'))
        for lemma in ('мальчик', 'малыш'):
            self.assertTrue(_has_sense(lemma, 'sense:review_boy'))

    def test_ending_law_and_lace_rotation_senses_are_separate(self):
        self.assertTrue(_has_sense('закон', 'sense:review_law'))
        for lemma in ('закончить', 'законченный', 'заканчиваться'):
            self.assertTrue(_has_sense(lemma, 'sense:review_end'), lemma)
        for lemma, sense in (
            ('круг', 'sense:review_circle'), ('кружить', 'sense:review_rotation'),
            ('кружево', 'sense:review_lace'), ('кружевной', 'sense:review_lace'),
            ('гора', 'sense:mountain'), ('нагорный', 'sense:mountain'),
            ('угорать', 'sense:fumes'), ('угореть', 'sense:fumes'),
            ('гореть', 'sense:burn'),
        ):
            self.assertTrue(_has_sense(lemma, sense))

    def test_head_fish_and_firebrand_families_are_separate(self):
        for lemma, sense in (
            ('голова', 'sense:review_head'), ('главный', 'sense:review_leadership'),
            ('голавль', 'sense:review_fish'), ('голавлевый', 'sense:review_fish'),
            ('головешка', 'sense:firebrand'), ('головневый', 'sense:smut'),
        ):
            self.assertTrue(_has_sense(lemma, sense))
        self.assertTrue({'sense:firebrand', 'sense:smut'} <= _sense_set('головня'))

    def test_key_and_spring_keep_separate_readings(self):
        self.assertEqual(_sense_set('ключ'), {'sense:key', 'sense:key_spring'})
        self.assertTrue(_has_sense('ключник', 'sense:key'))

    def test_essence_existence_and_presence_are_separate(self):
        self.assertTrue(_has_sense('сущность', 'sense:essence'))
        self.assertTrue(_has_sense('существовать', 'sense:existence'))
        self.assertTrue(_has_sense('присутствие', 'sense:presence'))
        for lemma in ('суть', 'существо'):
            self.assertTrue({'sense:essence', 'sense:existence'} <= _sense_set(lemma))

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
                self.assertTrue(_has_sense(lemma, root))

    def test_white_squirrel_and_protein_words_stay_separate(self):
        expected = {
            'белый': 'sense:white',
            'белка': 'sense:squirrel',
            'белок': 'sense:protein',
        }
        for lemma, root in expected.items():
            with self.subTest(lemma=lemma):
                self.assertTrue(_has_sense(lemma, root))

    def test_know_and_nobility_readings_of_znat_are_separate(self):
        self.assertTrue({'sense:cognize', 'sense:nobility'} <= _sense_set('знать'))
        self.assertTrue(_has_sense('знатность', 'sense:nobility'))
        self.assertEqual(_sense_set('знатный'), {'sense:nobility'})

    def test_write_and_urinate_readings_of_pisat_are_separate(self):
        for lemma in ('писать', 'писаться'):
            with self.subTest(lemma=lemma):
                self.assertTrue({'sense:review_writing', 'sense:urinate'} <= _sense_set(lemma))

    def test_light_and_society_readings_of_svet_are_separate(self):
        self.assertTrue(_has_sense('свет', 'sense:light'))
        self.assertTrue(_has_sense('светский', 'sense:society'))
        self.assertTrue(_has_sense('светить', 'sense:light'))

    def test_calling_and_designation_words_are_separate(self):
        self.assertTrue(_has_sense('звать', 'sense:review_calling'))
        self.assertTrue(_has_sense('название', 'sense:review_designation'))
        self.assertTrue({'sense:review_calling', 'sense:review_title'} <= _sense_set('звание'))

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
                self.assertTrue(_has_sense(lemma, root))
        self.assertTrue(set(structures_of('свет')).isdisjoint(structures_of('светский')))

    def test_counting_reading_and_honor_senses_are_separate(self):
        self.assertTrue(set(structures_of('счет')).isdisjoint(structures_of('читать')))
        self.assertTrue(set(structures_of('расчет')).isdisjoint(structures_of('чтение')))
        self.assertTrue(set(structures_of('почет')).isdisjoint(structures_of('читать')))
        self.assertTrue(_has_sense('считать', 'sense:count'))
        self.assertIn(('root:read',), structures_of('считать'))
        self.assertTrue(_has_sense('почитать', 'sense:honor'))
        self.assertIn(('root:read',), structures_of('почитать'))

    def test_kosa_homonyms_keep_separate_readings(self):
        self.assertTrue({'sense:scythe', 'sense:hair_braid', 'sense:spit_landform'} <= _sense_set('коса'))
        self.assertTrue({'sense:scythe', 'sense:oblique'} <= _sense_set('косить'))
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
    ('соты', 'сотня'),
    ('белый', 'белок'),
    ('белый', 'белка'),
    ('белый', 'белковый'),
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
    def test_confirmed_censorly_word_match_reports_from_last_day(self):
        """Keep the latest confirmed production reports reproducible locally."""
        expected_open = (
            ('математика', 'Математический'),
            ('это', 'эти'),
            ('ион', 'ионно'),
            ('вещь', 'вещевой'),
            ('вещество', 'вещественный'),
            ('свой', 'свойский'),
            ('присваивание', 'присвоить'),
            ('рассуждение', 'рассуждать'),
            ('ссуда', 'ссужать'),
        )
        expected_closed = (
            ('жалование', 'жалевшая'),
            ('требовать', 'употребления'),
            ('связано', 'вяжущих'),
            ('электрон', 'электрическое'),
            ('третий', 'Трои'),
            ('имя', 'им'),
            ('три', 'Трои'),
            ('вещи', 'веществ'),
            ('свойство', 'своими'),
            ('суд', 'рассуждения'),
            ('суд', 'ссуда'),
            ('суд', 'пересуды'),
            ('свой', 'свояк'),
            ('свой', 'присвоение'),
        )
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in expected_open:
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in expected_closed:
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_troy_forms_stay_with_troy_not_three(self):
        """Troy's case forms are not the homographic three/троить family."""
        troy_forms = ('Троя', 'Трои', 'Трою', 'Троей', 'Троею')
        troy_adjectives = ('троянский', 'троянская', 'троянское', 'троянские', 'троянского')

        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for left in troy_forms + troy_adjectives:
            for right in troy_forms + troy_adjectives:
                with self.subTest(expected='OPEN', guess=left, target=right):
                    self.assertTrue(gameplay_opens(left, right))
                    self.assertTrue(gameplay_opens(right, left))
            for right in ('три', '3', 'троить'):
                with self.subTest(expected='CLOSED', guess=left, target=right):
                    self.assertFalse(gameplay_opens(left, right))
                    self.assertFalse(gameplay_opens(right, left))

    def test_russian_language_stub_opens_russian(self):
        tokens = [{'kind': 'content', 'surface': 'русский', 'lemma': 'русский'}]
        self.assertEqual(matching_lemmas(tokens, 'рус'), {'русский'})

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

    def test_reviewed_opaque_and_alternating_roots_open_in_gameplay(self):
        tokens = [
            {'kind': 'content', 'surface': 'империя', 'lemma': 'империя'},
            {'kind': 'content', 'surface': 'император', 'lemma': 'император'},
            {'kind': 'content', 'surface': 'Наполеон', 'lemma': 'наполеон'},
            {'kind': 'content', 'surface': 'Наполеоновский', 'lemma': 'наполеоновский'},
        ]
        self.assertIn('император', matching_lemmas(tokens, 'империя'))
        self.assertIn('империя', matching_lemmas(tokens, 'император'))
        self.assertIn('наполеоновский', matching_lemmas(tokens, 'Наполеон'))
        self.assertIn('наполеон', matching_lemmas(tokens, 'Наполеоновский'))

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

    def test_article_wife_form_does_not_use_zhenya_reading(self):
        payload = build_puzzle_payload(
            wiki_title='Проверка',
            body_text='Евгений говорил о жене. Он встретил жену.',
        )
        hits = lemmas_matching_guess(payload, lemma_of('евгений'), normalize_surface('евгений'))
        self.assertNotIn('жена', hits)

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


_GRAPH = Path(__file__).resolve().parents[1] / 'puzzles' / 'censorly' / 'lexical' / 'data' / 'ru_graph.tsv.gz'
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
