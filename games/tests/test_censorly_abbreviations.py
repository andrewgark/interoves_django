"""Regression tests for the approved one-word abbreviation aliases."""

from django.test import SimpleTestCase

from games.censorly.lexical.russian.reading_overrides import LANGUAGE_LABEL_CONTEXTS
from games.censorly.lexical.match import _GuessPack, _Pack, _relation, matching_lemmas
from games.censorly.lexical.semantics import explain, opens
from games.censorly.tokenize import tokenize_text


APPROVED_ABBREVIATION_PAIRS = (
    ('килогерц', 'кгц'),
    ('мегагерц', 'мгц'),
    ('гигагерц', 'ггц'),
    ('терагерц', 'тгц'),
    ('килопаскаль', 'кпа'),
    ('мегапаскаль', 'мпа'),
    ('килоджоуль', 'кдж'),
    ('мегаджоуль', 'мдж'),
    ('мегабит', 'мбит'),
    ('гигабит', 'гбит'),
    ('гигабеккерель', 'гбк'),
    ('килобеккерель', 'кбк'),
    ('килокалория', 'ккал'),
    ('килоньютон', 'кн'),
    ('меганьютон', 'мн'),
    ('кандела', 'кд'),
    ('люкс', 'лк'),
    ('люмен', 'лм'),
    ('атмосфера', 'атм'),
    ('паскаль', 'па'),
    ('миллиграмм', 'мг'),
    ('микрограмм', 'мкг'),
    ('микрометр', 'мкм'),
    ('нанометр', 'нм'),
    ('милливольт', 'мв'),
    ('киловольт', 'кв'),
    ('килобайт', 'кбайт'),
    ('мегабайт', 'мбайт'),
    ('гигабайт', 'гбайт'),
    ('медицинский', 'мед'),
    ('преподаватель', 'препод'),
    ('администратор', 'админ'),
    ('заместитель', 'зам'),
    ('заведующий', 'зав'),
    ('информация', 'инфа'),
    ('программа', 'прога'),
    ('лаборатория', 'лаба'),
)


class CensorlyAbbreviationAliasTests(SimpleTestCase):
    def test_all_approved_pairs_are_bidirectional_aliases(self):
        self.assertEqual(len(APPROVED_ABBREVIATION_PAIRS), 37)
        for full, abbreviation in APPROVED_ABBREVIATION_PAIRS:
            with self.subTest(full=full, abbreviation=abbreviation):
                self.assertEqual(explain(full, abbreviation), 'alias')
                self.assertEqual(explain(abbreviation, full), 'alias')
                self.assertTrue(opens(full, abbreviation))
                self.assertTrue(opens(abbreviation, full))

    def test_casefolding_and_yo_normalization_are_preserved(self):
        self.assertEqual(explain('КГЦ', 'Килогерц'), 'alias')
        self.assertEqual(explain('мГц', 'МЕГАГЕРЦ'), 'alias')

    def test_bit_and_byte_prefix_aliases_are_bidirectional(self):
        pairs = (
            ('килобит', 'кбит'),
            ('мегабит', 'мбит'),
            ('гигабит', 'гбит'),
            ('терабит', 'тбит'),
            ('петабит', 'пбит'),
            ('эксабит', 'эбит'),
            ('килобайт', 'кб'),
            ('мегабайт', 'мб'),
            ('гигабайт', 'гб'),
            ('терабайт', 'тб'),
            ('петабайт', 'пб'),
            ('эксабайт', 'эб'),
        )
        for full, abbreviation in pairs:
            with self.subTest(full=full, abbreviation=abbreviation):
                self.assertEqual(explain(full, abbreviation), 'alias')
                self.assertEqual(explain(abbreviation, full), 'alias')
                self.assertTrue(opens(full, abbreviation))
                self.assertTrue(opens(abbreviation, full))

    def test_bit_and_byte_aliases_do_not_cross_units_or_prefixes(self):
        for left, right in (
            ('тбит', 'тбайт'),
            ('мбит', 'мбайт'),
            ('мб', 'мбит'),
            ('тб', 'гб'),
            ('пбит', 'эбит'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

    def test_alias_groups_do_not_create_unwanted_transitivity(self):
        for left, right in (
            ('кгц', 'мегагерц'),
            ('кпа', 'мегапаскаль'),
            ('мг', 'микрограмм'),
            ('мед', 'информация'),
            ('препод', 'админ'),
            ('кд', 'люмен'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

    def test_existing_aliases_and_rejected_abbreviations_are_unchanged(self):
        for left, right in (
            ('ес', 'евросоюз'),
            ('универ', 'университет'),
            ('кг', 'килограмм'),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(explain(left, right), 'alias')
                self.assertEqual(explain(right, left), 'alias')

        for left, right in (
            ('вуз', 'высшее'),
            ('сми', 'средство'),
            ('рф', 'россия'),
            ('нато', 'организация'),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(opens(left, right))
                self.assertFalse(opens(right, left))

    def test_multiword_or_punctuated_expansions_are_not_added(self):
        self.assertFalse(opens('кгц', 'килогерц в секунду'))
        self.assertFalse(opens('зам.', 'заместитель'))


class CensorlyLanguageLabelRootTests(SimpleTestCase):
    def test_language_labels_open_from_the_whole_root_family(self):
        for guess, target in (
            ('франция', 'фр'),
            ('француз', 'фр'),
            ('франция', 'франц'),
            ('россия', 'рус'),
            ('русь', 'рус'),
            ('англия', 'англ'),
            ('германия', 'нем'),
            ('немец', 'нем'),
            ('италия', 'итал'),
            ('испания', 'исп'),
            ('япония', 'яп'),
            ('украина', 'укр'),
            ('фр', 'франция'),
            ('рус', 'россия'),
        ):
            with self.subTest(guess=guess, target=target):
                self.assertEqual(_relation(_GuessPack(guess), _Pack(target)), 'root')

    def test_greek_label_opens_through_the_proper_name_pair(self):
        self.assertEqual(_relation(_GuessPack('греция'), _Pack('греч')), 'proper')
        self.assertEqual(explain('греция', 'греч'), 'proper')

    def test_homographic_stubs_do_not_inherit_the_country_family(self):
        for guess, target in (('китаец', 'кит'), ('кит', 'китай')):
            with self.subTest(guess=guess, target=target):
                self.assertFalse(_relation(_GuessPack(guess), _Pack(target)))
                self.assertFalse(opens(guess, target))

    def test_article_labels_open_but_nem_contexts_stay_split(self):
        tokens = tokenize_text(
            'Париж (фр. Paris), Москва (рус. Москва), Берлин (нем. Berlin). '
            'Он нем и глух.'
        )
        self.assertIn('фр', matching_lemmas(tokens, 'франция'))
        self.assertTrue(matching_lemmas(tokens, 'россия'))
        self.assertEqual(
            matching_lemmas(tokens, 'германия'), {'__censorly_nem_german__'},
        )

    def test_homographic_labels_split_by_position(self):
        tokens = tokenize_text(
            'Пекин (кит. 北京), Анкара (тур. Ankara), Минск (бел. Мінск), '
            'кошка (от лат. felis), Лиссабон (порт. Lisboa), '
            'Копенгаген (дат. København). '
            'Там плавал синий кит. Мы купили тур. Корабль вошёл в порт. Нет дат.'
        )
        by_surface = {}
        for token in tokens:
            if token['kind'] == 'content':
                by_surface.setdefault(token['surface'], []).append(token['lemma'])
        for label in ('кит', 'тур', 'бел', 'лат', 'порт', 'дат'):
            with self.subTest(label=label):
                self.assertEqual(
                    by_surface[label][0], LANGUAGE_LABEL_CONTEXTS[label],
                )
        for label in ('кит', 'тур', 'порт', 'дат'):
            with self.subTest(sentence_final=label):
                self.assertNotIn(by_surface[label][1], LANGUAGE_LABEL_CONTEXTS.values())
        for guess, label in (
            ('китай', 'кит'), ('китаец', 'кит'), ('турция', 'тур'),
            ('белоруссия', 'бел'), ('латынь', 'лат'),
            ('португалия', 'порт'), ('дания', 'дат'),
        ):
            with self.subTest(guess=guess):
                self.assertIn(LANGUAGE_LABEL_CONTEXTS[label], matching_lemmas(tokens, guess))
        for guess in ('китай', 'турция', 'португалия', 'дания'):
            with self.subTest(country=guess):
                self.assertFalse(
                    matching_lemmas(tokens, guess) - set(LANGUAGE_LABEL_CONTEXTS.values()),
                )
        for guess in ('гавань', 'турист', 'белый'):
            with self.subTest(ordinary=guess):
                self.assertFalse(
                    matching_lemmas(tokens, guess) & set(LANGUAGE_LABEL_CONTEXTS.values()),
                )
