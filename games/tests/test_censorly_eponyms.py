"""Regression coverage for the approved Censorly eponym pairs."""

from django.test import SimpleTestCase

from games.censorly.lexical.semantics import explain, opens


EPONYMS = (
    ('Ленин', 'ленинизм'),
    ('Лоуренс', 'лоуренсий'),
    ('Менделеев', 'менделевий'),
    ('Нобель', 'нобелий'),
    ('Гёте', 'гётит'),
    ('Антонов', 'антоновский'),
    ('Клемент', 'клементина'),
    ('Бандера', 'бандеровщина'),
    ('Гитлер', 'гитлериана'),
    ('Фауст', 'фаустианство'),
    ('Свердлов', 'свердловский'),
    ('Пушкин', 'пушкинистика'),
    ('Фергюсон', 'фергусонит'),
)


class ApprovedEponymPairTests(SimpleTestCase):
    def test_pairs_open_in_both_directions(self):
        for name, derivative in EPONYMS:
            self.assertEqual(explain(name, derivative), 'proper')
            self.assertEqual(explain(derivative, name), 'proper')
            self.assertTrue(opens(name, derivative))
            self.assertTrue(opens(derivative, name))

    def test_pairs_support_inflected_forms(self):
        cases = (
            ('Ленина', 'ленинизма'),
            ('Лоуренса', 'лоуренсия'),
            ('Менделееву', 'менделевием'),
            ('Нобеля', 'нобелием'),
            ('Гёте', 'гётитом'),
            ('Антонова', 'антоновским'),
            ('Клемента', 'клементиной'),
            ('Гитлера', 'гитлерианой'),
            ('Свердлова', 'свердловским'),
        )
        for name, derivative in cases:
            self.assertEqual(explain(name, derivative), 'proper')
            self.assertEqual(explain(derivative, name), 'proper')

    def test_pairs_do_not_create_transitive_edges(self):
        unrelated = (
            ('ленин', 'менделеев'),
            ('ленинизм', 'менделевий'),
            ('лоуренсий', 'нобелий'),
            ('гётит', 'антоновский'),
        )
        for left, right in unrelated:
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')

        # пушкин is a legacy member of CLUSTERS; the new pair must not make
        # its derivative join that cluster.
        self.assertFalse(opens('пушкинистика', 'пушкинский'))
        self.assertFalse(opens('пушкинский', 'пушкинистика'))

    def test_pairs_do_not_open_common_name_or_homonym_edges(self):
        unrelated = (
            ('Антон', 'антоновский'),
            ('Ленин', 'ленинист'),
            ('Нобель', 'нобелевский'),
            ('Гёте', 'гетитовый'),
        )
        for left, right in unrelated:
            self.assertFalse(opens(left, right), f'{left}/{right}')
            self.assertFalse(opens(right, left), f'{right}/{left}')
