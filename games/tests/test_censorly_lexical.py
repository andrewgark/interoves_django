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

from games.puzzles.censorly.article.redact import lemmas_matching_guess
from games.puzzles.censorly.lexical.graph.graph import edge_count, neighbors
from games.puzzles.censorly.lexical.match import matching_lemmas
from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.lexical.match.dispatcher import backend_name
from games.puzzles.censorly.lexical.match.generic_backend import CAPABILITIES as GENERIC_CAPS
from games.puzzles.censorly.lexical.match.resolver import (
    describe_token,
    open_prepared,
    open_targets,
    prepare_target,
    related,
    relation_kind,
)
from games.puzzles.censorly.lexical.match.rootbank import structures_of
from games.puzzles.censorly.lexical.russian import CAPABILITIES as RUSSIAN_CAPS
from games.puzzles.censorly.lexical.russian.context import inflection_matches, occurrence_of
from games.puzzles.censorly.lexical.russian.decisions import REJECT
from games.puzzles.censorly.lexical.russian.morphology import cognate_lemmas
from games.puzzles.censorly.text.normalize import lemma_of, normalize_surface
from games.puzzles.censorly.text.roots import opens_with
from games.puzzles.censorly.text.tokenize import build_puzzle_payload, tokenize_text


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

    def test_reported_censorly_false_positives_stay_closed(self):
        from games.puzzles.censorly.lexical.match import _GuessPack, _Pack, _relation

        for guess, target in (
            ('приводит', 'извести'),
            ('цветы', 'цвета'),
            ('изображались', 'образом'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(_relation(_GuessPack(guess), _Pack(target)))

    def test_data_forms_do_not_open_give_through_the_participle(self):
        from games.puzzles.censorly.lexical.match import _Pack, _relation

        for left, right in (('данным', 'давать'), ('давать', 'данным'), ('данный', 'дать')):
            self.assertFalse(_relation(_Pack(left), _Pack(right)), f'{left}/{right}')
        self.assertTrue(_relation(_Pack('данным'), _Pack('данные')))
        self.assertTrue(_relation(_Pack('дан'), _Pack('дать')))

    def test_rod_family_keeps_breed_revival_and_birth_apart(self):
        from games.puzzles.censorly.lexical.match import _Pack, _relation

        for left, right in (
            ('порода', 'породистый'), ('порода', 'породный'),
            ('возрождение', 'возродить'), ('вырождение', 'выродок'),
            ('зародыш', 'зарождение'), ('породить', 'порождение'),
            ('род', 'родовитый'), ('родить', 'рождение'),
        ):
            self.assertTrue(_relation(_Pack(left), _Pack(right)), f'{left}/{right}')
        for left, right in (
            ('порода', 'возрождения'), ('порода', 'рождение'),
            ('порода', 'породить'), ('возрождение', 'рождение'),
            ('зародыш', 'роды'),
        ):
            self.assertFalse(_relation(_Pack(left), _Pack(right)), f'{left}/{right}')

    def test_two_family_senses_are_assigned_to_distinct_clusters(self):
        from games.puzzles.censorly.lexical.match import _GuessPack, _Pack, _relation
        from games.puzzles.censorly.lexical.splits import CLUSTERS

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

        self.assertTrue(_has_sense('двойня', 'sense:twin_birth'))
        self.assertTrue(_has_sense('двойник', 'sense:lookalike'))
        self.assertFalse(_relation(_GuessPack('двойня'), _Pack('двойник')))
        self.assertFalse(_relation(_GuessPack('двойник'), _Pack('двойня')))
        self.assertTrue(_relation(_GuessPack('двойня'), _Pack('двойняшка')))
        self.assertTrue(_relation(_GuessPack('двойник'), _Pack('двойниковый')))

    def test_binding_and_contact_senses_are_separate(self):
        from games.puzzles.censorly.lexical.match import _GuessPack, _Pack, _relation

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
        from games.puzzles.censorly.lexical.match import _GuessPack, _Pack, _relation

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
    def test_reviewed_organization_and_university_pairs_open_in_gameplay(self):
        pairs = (
            ('Гарвард', 'гарвардский'), ('Кембридж', 'кембриджский'),
            ('Оксфорд', 'оксфордский'), ('Принстон', 'принстонский'),
            ('Стэнфорд', 'стэнфордский'), ('Корнелл', 'корнеллский'),
            ('Дартмут', 'дартмутский'), ('Интерпол', 'интерполовский'),
            ('НАТО', 'натовский'), ('Коминтерн', 'коминтерновский'),
            ('НАСА', 'насовский'), ('Евросоюз', 'евросоюзовский'),
            ('ЖЭК', 'жэковский'), ('ВОЗ', 'возовский'),
        )
        for name, adjective in pairs:
            with self.subTest(name=name, adjective=adjective):
                self.assertIn(adjective, matching_lemmas(tokenize_text(adjective), name))
                self.assertTrue(matching_lemmas(tokenize_text(name), adjective))

    def test_granata_complaints_open_barrel_rifle_and_battle_words(self):
        for guess, target in (
            ('ствол', 'подствольных'), ('ствол', 'стволик'),
            ('винтовка', 'винтовочных'), ('винтовка', 'винтовой'),
            ('бой', 'боевой'), ('бой', 'боец'), ('бой', 'битва'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertTrue(matching_lemmas(tokenize_text(target), guess))

    def test_battle_noun_does_not_open_hitting_words(self):
        for target in ('бить', 'обои', 'обойный', 'прибойный', 'бесперебойный', 'бойкий'):
            with self.subTest(target=target):
                self.assertFalse(matching_lemmas(tokenize_text(target), 'бой'))
        self.assertTrue(matching_lemmas(tokenize_text('прибойный'), 'прибой'))

    def test_who_acronym_does_not_link_common_noun_voz(self):
        for common_noun in ('воз', 'воза', 'возы'):
            with self.subTest(common_noun=common_noun):
                self.assertNotIn('возовский', matching_lemmas(tokenize_text('возовский'), common_noun))
                self.assertFalse(matching_lemmas(tokenize_text(common_noun), 'возовский'))

    def test_yale_name_opens_adjective_in_gameplay(self):
        for guess, target, expected in (
            ('Йель', 'Йельский', 'йельский'),
            ('Йель', 'Йельского', 'йельский'),
            ('Йельский', 'Йель', 'йель'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertIn(expected, matching_lemmas(tokenize_text(target), guess))

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
            ('гниют', 'гнилой'),
            ('шествие', 'шествовать'),
            ('сосуд', 'сосудистый'),
            ('вес', 'весомый'),
            ('способный', 'способность'),
            ('светящийся', 'светило'),
            ('победа', 'побеждать'),
            ('округ', 'окружной'),
            ('окружать', 'окружающими'),
            ('круг', 'круглый'),
            ('целей', 'прицельный'),
            ('целее', 'цельного'),
            ('правило', 'неправильно'),
            ('править', 'исправлять'),
            ('правительство', 'межправительственный'),
            ('управление', 'управленец'),
            ('правый', 'ультраправый'),
            ('правый', 'правша'),
            ('правда', 'прав'),
            ('правый', 'правота'),
            ('неправый', 'правда'),
            ('оправиться', 'поправился'),
            ('поправка', 'поправился'),
            ('справиться', 'справка'),
            ('оправа', 'оправить'),
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
            ('гниют', 'отгоняет'),
            ('нашествием', 'прошедший'),
            ('сосудистая', 'посуда'),
            ('взвесь', 'перевешивает'),
            ('способы', 'приспособление'),
            ('светящийся', 'светлее'),
            ('бедный', 'побеждать'),
            ('округа', 'окружающими'),
            ('целей', 'цельного'),
            ('целей', 'целостность'),
            ('править', 'неправильно'),
            ('ультраправый', 'антиправительственный'),
            ('неправый', 'справа'),
            ('справка', 'справить'),
            ('оправа', 'оправиться'),
            ('вправить', 'оправа'),
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

    def test_sel_family_splits_settling_by_prefix_meaning(self):
        """Поселение, население, выселение and переселение are different acts."""
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('население', 'населенный'), ('население', 'перенаселение'),
            ('поселение', 'поселиться'), ('селиться', 'поселенец'),
            ('переселенец', 'переселение'), ('выселить', 'отселить'),
            ('вселиться', 'заселение'), ('расселение', 'расселить'),
            ('село', 'сельский'), ('вселенная', 'вселенский'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('поселения', 'Население'), ('поселение', 'переселение'),
            ('выселение', 'вселение'), ('заселить', 'население'),
            ('расселение', 'поселение'), ('село', 'поселение'),
            ('вселенная', 'вселение'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_kol_family_splits_ring_wheel_and_around(self):
        """Коло gave ring, wheel and «around» words that players do not link."""
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        # «ок.» in Wikipedia dates is read as около.
        tokens = [{'kind': 'content', 'surface': 'ок', 'lemma': 'около'}]
        self.assertEqual(matching_lemmas(tokens, 'кольцо'), set())
        for guess, target in (
            ('кольцо', 'кольчуга'), ('колечко', 'кольцевой'), ('кольцо', 'окольцевать'),
            ('колесо', 'колея'), ('колесо', 'коляска'), ('поколесить', 'колесо'),
            ('околица', 'окольный'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('кольцо', 'около'), ('кольцо', 'колесо'), ('колесо', 'окольный'),
            ('околица', 'кольцо'), ('околесица', 'околица'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

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
            {'kind': 'content', 'surface': 'кораллы', 'lemma': 'коралл'},
            {'kind': 'content', 'surface': 'коралловый', 'lemma': 'коралловый'},
        ]
        self.assertIn('император', matching_lemmas(tokens, 'империя'))
        self.assertIn('империя', matching_lemmas(tokens, 'император'))
        self.assertIn('наполеоновский', matching_lemmas(tokens, 'Наполеон'))
        self.assertIn('наполеон', matching_lemmas(tokens, 'Наполеоновский'))
        self.assertIn('коралловый', matching_lemmas(tokens, 'коралл'))
        self.assertIn('коралл', matching_lemmas(tokens, 'коралловых'))

    def test_garnet_family_opens_adjectives_but_not_grenade_launcher(self):
        tokens = [
            {'kind': 'content', 'surface': 'гранату', 'lemma': 'граната'},
            {'kind': 'content', 'surface': 'гранатовый', 'lemma': 'гранатовый'},
            {'kind': 'content', 'surface': 'гранатный', 'lemma': 'гранатный'},
            {'kind': 'content', 'surface': 'гранатомёт', 'lemma': 'гранатомет'},
        ]
        opened = {'граната', 'гранатовый', 'гранатный'}
        self.assertEqual(matching_lemmas(tokens, 'Граната'), opened)
        self.assertEqual(matching_lemmas(tokens, 'гранат'), opened)
        self.assertEqual(matching_lemmas(tokens, 'гранатовым'), opened)
        self.assertEqual(matching_lemmas(tokens, 'гранатомёт'), {'гранатомет'})

    def test_reviewed_opaque_sky_adjectives_open_from_their_nouns(self):
        tokens = [
            {'kind': 'content', 'surface': 'тюркского', 'lemma': 'тюркский'},
            {'kind': 'content', 'surface': 'татарского', 'lemma': 'татарский'},
            {'kind': 'content', 'surface': 'офицерский', 'lemma': 'офицерский'},
            {'kind': 'content', 'surface': 'казахской', 'lemma': 'казахский'},
        ]
        self.assertIn('тюркский', matching_lemmas(tokens, 'тюрк'))
        self.assertIn('тюркский', matching_lemmas(tokens, 'тюрки'))
        self.assertIn('татарский', matching_lemmas(tokens, 'татарин'))
        self.assertIn('офицерский', matching_lemmas(tokens, 'офицер'))
        self.assertIn('казахский', matching_lemmas(tokens, 'казах'))

    def test_reviewed_nest_anchors_open_the_anchor_family(self):
        tokens = [
            {'kind': 'content', 'surface': 'обычно', 'lemma': 'обычно'},
            {'kind': 'content', 'surface': 'часто', 'lemma': 'часто'},
            {'kind': 'content', 'surface': 'вид', 'lemma': 'вид'},
            {'kind': 'content', 'surface': 'исторически', 'lemma': 'исторически'},
            {'kind': 'content', 'surface': 'неверно', 'lemma': 'неверно'},
            {'kind': 'content', 'surface': 'вероятность', 'lemma': 'вероятность'},
            {'kind': 'content', 'surface': 'треугольник', 'lemma': 'треугольник'},
        ]
        self.assertIn('обычно', matching_lemmas(tokens, 'обыкновенный'))
        self.assertIn('часто', matching_lemmas(tokens, 'частота'))
        self.assertIn('вид', matching_lemmas(tokens, 'подвид'))
        self.assertIn('исторически', matching_lemmas(tokens, 'историк'))
        self.assertIn('неверно', matching_lemmas(tokens, 'неверный'))
        self.assertIn('вероятность', matching_lemmas(tokens, 'невероятный'))
        # Anchors copy one sense, and compounds are never anchored.
        self.assertNotIn('вид', matching_lemmas(tokens, 'видеть'))
        self.assertNotIn('часто', matching_lemmas(tokens, 'часть'))
        self.assertNotIn('вероятность', matching_lemmas(tokens, 'верить'))
        self.assertNotIn('треугольник', matching_lemmas(tokens, 'три'))

    def test_corpus_batch_a1_families(self):
        tokens = [
            {'kind': 'content', 'surface': 'компании', 'lemma': 'компания'},
            {'kind': 'content', 'surface': 'организации', 'lemma': 'организация'},
            {'kind': 'content', 'surface': 'стилистика', 'lemma': 'стилистика'},
            {'kind': 'content', 'surface': 'министерство', 'lemma': 'министерство'},
            {'kind': 'content', 'surface': 'детерминироваться', 'lemma': 'детерминироваться'},
            {'kind': 'content', 'surface': 'тематика', 'lemma': 'тематика'},
        ]
        self.assertIn('компания', matching_lemmas(tokens, 'авиакомпания') | matching_lemmas(tokens, 'компания'))
        self.assertIn('организация', matching_lemmas(tokens, 'организовать'))
        self.assertIn('стилистика', matching_lemmas(tokens, 'стиль'))
        self.assertIn('министерство', matching_lemmas(tokens, 'министр'))
        self.assertIn('детерминироваться', matching_lemmas(tokens, 'детерминизм'))
        self.assertIn('тематика', matching_lemmas(tokens, 'тема'))
        # Separate roots for diverging senses and spelling-only coincidences.
        self.assertNotIn('организация', matching_lemmas(tokens, 'организм'))
        self.assertNotIn('стилистика', matching_lemmas(tokens, 'стол'))
        self.assertNotIn('детерминироваться', matching_lemmas(tokens, 'термин'))
        self.assertNotIn('тематика', matching_lemmas(tokens, 'темный'))

    def test_corpus_batch_a2_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('народ', 'международный'), ('работа', 'разработчик'), ('поэт', 'поэма'),
            ('датировка', 'дата'), ('организм', 'органический'), ('революция', 'революционный'),
            ('редакция', 'редактор'), ('клавиша', 'клавиатура'), ('минимум', 'минимальный'),
            ('генерал', 'генеральский'), ('генеральный', 'генерализация'), ('граф', 'графиня'),
            ('карьера', 'карьерист'), ('вулкан', 'вулканический'), ('идея', 'идейный'),
            ('корона', 'коронация'), ('уничтожить', 'уничтожение'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        # Rejected Tikhonov roots keep their diverging senses apart.
        for guess, target in (
            ('дата', 'датский'), ('генерал', 'генеральный'), ('график', 'граф'),
            ('чайхана', 'хан'), ('эра', 'эрэс'), ('диалект', 'диалектика'),
            ('вулкан', 'вулканизация'), ('идея', 'идеал'), ('персонаж', 'персонал'),
            ('диск', 'дискотека'), ('трон', 'позитрон'), ('номинация', 'номинатив'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_corpus_batch_a3_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('мода', 'модный'), ('нацизм', 'нацистский'), ('мобильный', 'мобильность'),
            ('библия', 'библейский'), ('атеизм', 'теизм'), ('диктатура', 'диктатор'),
            ('идеал', 'идеальный'), ('вирус', 'вирусный'), ('турок', 'турецкий'),
            ('реакция', 'реагировать'), ('регулярный', 'иррегулярный'),
            ('регулировать', 'урегулирование'), ('промежуток', 'промежуточный'),
            ('сегодня', 'сегодняшний'), ('терапия', 'терапевт'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('мода', 'модификация'), ('мобильный', 'автомобиль'), ('библия', 'библиотека'),
            ('ода', 'электрод'), ('диктатура', 'диктовать'), ('идея', 'идеал'),
            ('регулировать', 'иррегулярный'), ('атлас', 'атласный'),
            # Nazism keeps its own root: нация opens neither way.
            ('нация', 'нацист'), ('нация', 'нацизм'), ('нацист', 'национальный'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_corpus_batch_a4_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('полиция', 'полицейский'), ('симфония', 'симфонический'), ('модель', 'моделирование'),
            ('католический', 'католик'), ('этика', 'этический'), ('диагноз', 'диагностика'),
            ('касса', 'кассир'), ('экспорт', 'импорт'), ('оптимизация', 'оптимальный'),
            ('соло', 'солист'), ('действие', 'задействовать'), ('коллектив', 'коллективный'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('этика', 'этикет'), ('хроника', 'хронический'), ('хроника', 'синхронный'),
            ('касса', 'кассация'), ('таксон', 'такси'), ('плантация', 'трансплантация'),
            ('швейцарец', 'швейцар'), ('тайский', 'тайна'), ('экспорт', 'порт'),
            ('мода', 'модификация'), ('ликвидация', 'ликвидный'), ('оптимальный', 'оптимизм'),
            ('персонал', 'персональный'), ('посольство', 'сольный'), ('демон', 'демонстрация'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_compound_batch_c1_families(self):
        """A reviewed compound opens its own family, never by one component."""
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('самолет', 'самолетный'), ('столетие', 'столетний'), ('треугольник', 'треугольный'),
            ('иностранный', 'иностранец'), ('взаимодействие', 'взаимодействовать'),
            ('законодательство', 'законодательный'), ('экология', 'экологический'),
            ('телевидение', 'телевизор'), ('фотография', 'фотограф'),
            ('автобиография', 'биография'), ('бомба', 'бомбардировщик'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('лет', 'самолет'), ('лето', 'столетие'), ('угол', 'треугольник'),
            ('прямоугольный', 'треугольник'), ('страна', 'иностранный'),
            ('действие', 'взаимодействие'), ('закон', 'законодательство'),
            ('логика', 'экология'), ('телевидение', 'телефон'), ('граф', 'фотография'),
            ('паровоз', 'пароход'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_compound_batch_c2_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('ежегодный', 'ежегодник'), ('взаимосвязь', 'взаимосвязанный'), ('доброволец', 'добровольный'),
            ('космос', 'космонавт'), ('телеграф', 'телеграмма'), ('самоубийство', 'самоубийца'),
            ('основоположник', 'основополагающий'), ('демократия', 'демократизация'),
            ('археолог', 'археология'), ('статуя', 'статуэтка'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        for guess, target in (
            ('год', 'ежегодный'), ('ежегодный', 'ежедневный'), ('связь', 'взаимосвязь'),
            ('кино', 'кинотеатр'), ('театр', 'кинотеатр'), ('убийство', 'самоубийство'),
            ('вода', 'водопровод'), ('трубопровод', 'водотрубный'), ('правосудие', 'суд'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_nest_anchor_batch_w4_2_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('чиновничество', 'чиновник'), ('свинина', 'свинья'), ('студент', 'студенческий'),
            ('медведь', 'медвежонок'), ('вино', 'винный'),
            ('век', 'увековечить'), ('тело', 'антитело'), ('москва', 'подмосковье'),
            ('тринадцать', 'тринадцатый'), ('мягкий', 'смягчить'), ('мягче', 'мягкость'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        # Rejected nest-mates: different senses, homonyms and compounds.
        for guess, target in (
            ('вина', 'винный'), ('актер', 'актуальный'), ('текст', 'текстиль'),
            ('радиус', 'радиация'), ('лень', 'ленный'), ('гора', 'угорский'),
            ('идея', 'идеальный'), ('вид', 'видовой'), ('банк', 'банкомат'),
            ('три', 'тринадцатый'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_track_b_batch_1_families(self):
        def gameplay_opens(guess, target):
            return bool(matching_lemmas(tokenize_text(target), guess))

        for guess, target in (
            ('блок', 'блокировать'), ('баланс', 'дисбаланс'), ('витамин', 'авитаминоз'),
            ('прогресс', 'прогрессивный'), ('интервью', 'интервьюер'), ('ферма', 'фермер'),
            ('мигрант', 'иммигрант'), ('ясный', 'выяснить'), ('интенсивный', 'интенсивность'),
            ('нота', 'нотный'), ('такси', 'таксист'), ('интернет', 'интернетовский'),
            ('прогноз', 'спрогнозировать'), ('визит', 'визитка'), ('ходить', 'доходить'),
        ):
            with self.subTest(expected='OPEN', guess=guess, target=target):
                self.assertTrue(gameplay_opens(guess, target))
        # Rejected Tikhonov roots and look-alikes keep their senses apart.
        for guess, target in (
            ('нота', 'нотификация'), ('ферма', 'фермий'), ('такси', 'таксономия'),
            ('реакция', 'реактор'), ('блокнот', 'блок'), ('агрессия', 'прогресс'),
            ('призрак', 'приз'), ('склон', 'клон'), ('радиус', 'радио'),
            ('доход', 'доходить'),
        ):
            with self.subTest(expected='CLOSED', guess=guess, target=target):
                self.assertFalse(gameplay_opens(guess, target))

    def test_adverb_rule_opens_the_adjective_family(self):
        tokens = [
            {'kind': 'content', 'surface': 'примерно', 'lemma': 'примерно'},
            {'kind': 'content', 'surface': 'экономически', 'lemma': 'экономически'},
            {'kind': 'content', 'surface': 'по-русски', 'lemma': 'по-русски'},
            {'kind': 'content', 'surface': 'по-новому', 'lemma': 'по-новому'},
        ]
        self.assertIn('примерно', matching_lemmas(tokens, 'пример'))
        self.assertIn('экономически', matching_lemmas(tokens, 'экономика'))
        self.assertIn('по-русски', matching_lemmas(tokens, 'русский'))
        self.assertIn('по-новому', matching_lemmas(tokens, 'новость'))

    def test_adverb_rule_exceptions_are_not_generated(self):
        from games.puzzles.censorly.lexical.match.rootbank import ADVERB_BASES

        for adverb in ('совершенно', 'видимо', 'конечно', 'именно', 'довольно'):
            self.assertNotIn(adverb, ADVERB_BASES)

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
        from games.puzzles.censorly.lexical.russian.compiler import compile_graph
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
