"""Regression tests for the approved one-word abbreviation aliases."""

from django.test import SimpleTestCase

from games.censorly.lexical.semantics import explain, opens


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
