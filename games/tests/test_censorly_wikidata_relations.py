"""Regression tests for the approved Wikidata geographic relations."""

from django.test import SimpleTestCase

from games.puzzles.censorly.lexical.semantics import explain, opens


WIKIDATA_GEOGRAPHIC_PAIRS = (
    ('эфиопия', 'эфиоп'),
    ('эфиопия', 'эфиопка'),
    ('великобритания', 'британец'),
    ('великобритания', 'британка'),
    ('бразилия', 'бразилец'),
    ('бразилия', 'бразильянка'),
    ('канада', 'канадец'),
    ('канада', 'канадка'),
    ('беларусь', 'белорус'),
    ('беларусь', 'белоруска'),
    ('исландия', 'исландец'),
    ('исландия', 'исландка'),
    ('эстония', 'эстонка'),
    ('норвегия', 'норвежка'),
    ('латвия', 'латвиец'),
    ('латвия', 'латвийка'),
    ('украина', 'украинец'),
    ('украина', 'украинка'),
    ('торонто', 'торонтец'),
    ('торонто', 'торонтка'),
    ('куляб', 'кулябец'),
    ('актобе', 'актюбинец'),
    ('актобе', 'актюбинка'),
    ('кишинёв', 'кишинёвцы'),
    ('тель-авив', 'тель-авивец'),
    ('вифлеем', 'вифлеемляне'),
    ('вифлеем', 'вифлеемлянин'),
    ('вифлеем', 'вифлеемлянка'),
    ('баку', 'бакинец'),
    ('тбилиси', 'тбилисец'),
    ('тбилиси', 'тбилиска'),
    ('камышин', 'камышанин'),
    ('камышин', 'камышанка'),
)


class WikidataGeographicRelationTests(SimpleTestCase):
    def test_all_approved_pairs_are_explicit_and_bidirectional(self):
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS

        relations = set(PAIR_RELATIONS)
        self.assertEqual(len(WIKIDATA_GEOGRAPHIC_PAIRS), 33)
        for left, right in WIKIDATA_GEOGRAPHIC_PAIRS:
            with self.subTest(left=left, right=right):
                self.assertIn((left, right), relations)
                self.assertEqual(explain(left, right), 'proper')
                self.assertEqual(explain(right, left), 'proper')
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

    def test_rejected_pilot_pairs_are_not_added(self):
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS

        relations = set(PAIR_RELATIONS)
        rejected = (
            ('актобе', 'актюбинцы'),
            ('бельцы', 'бельчане'),
            ('владивосток', 'владивостокцы'),
            ('царицын', 'царицана'),
            ('царицын', 'царицанин'),
            ('царицын', 'царицанка'),
        )
        for pair in rejected:
            self.assertNotIn(pair, relations)

    def test_supported_inflected_forms_remain_bidirectional(self):
        cases = (
            ('Канады', 'канадкой'),
            ('Великобритании', 'британкой'),
            ('Баку', 'бакинцем'),
            ('Тбилиси', 'тбилисцами'),
            ('Тель-Авива', 'тель-авивцем'),
            ('Камышина', 'камышанкой'),
        )
        for left, right in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(explain(left, right), 'proper')
                self.assertEqual(explain(right, left), 'proper')

    def test_plural_only_and_hyphenated_surfaces_are_not_rewritten(self):
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS

        relations = set(PAIR_RELATIONS)
        self.assertIn(('кишинёв', 'кишинёвцы'), relations)
        self.assertNotIn(('кишинёв', 'кишинёвец'), relations)
        self.assertIn(('тель-авив', 'тель-авивец'), relations)
        self.assertNotIn(('тель', 'тель-авивец'), relations)

    def test_demonyms_from_the_same_source_open_each_other(self):
        by_source = {}
        for source, derivative in WIKIDATA_GEOGRAPHIC_PAIRS:
            by_source.setdefault(source, []).append(derivative)
        for source, derivatives in by_source.items():
            for index, left in enumerate(derivatives):
                for right in derivatives[index + 1:]:
                    with self.subTest(source=source, left=left, right=right):
                        self.assertTrue(opens(left, right))
                        self.assertTrue(opens(right, left))

    def test_germany_and_german_words_share_root_and_sense(self):
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS
        from games.puzzles.censorly.lexical.match.rootbank import structures_of

        family = (('fam:нем', 'sense:german'),)
        for lemma in (
            'германия', 'германский', 'германец', 'германка',
            'германизация', 'германизм', 'германист', 'германистика',
            'германизировать', 'германизироваться', 'германизированный',
            'немец', 'немецкий', 'немка', 'онемечивание',
        ):
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), family)

        for country, related in (
            ('Германия', 'германская'),
            ('Германия', 'немцы'),
            ('Германии', 'немкой'),
            ('Германия', 'немецкая'),
            ('германизации', 'немцев'),
        ):
            with self.subTest(country=country, related=related):
                self.assertEqual(explain(country, related), 'root')
                self.assertEqual(explain(related, country), 'root')

        for word in ('немой', 'неметь', 'немота'):
            with self.subTest(word=word):
                self.assertFalse(opens('Германия', word))
                self.assertFalse(opens(word, 'Германия'))

        for pair in (
            ('германия', 'германский'),
            ('германия', 'немец'),
            ('германия', 'немка'),
        ):
            self.assertNotIn(pair, PAIR_RELATIONS)

    def test_russia_and_russian_words_share_root_and_sense(self):
        from games.puzzles.censorly.lexical.match.proper_names import PAIR_RELATIONS
        from games.puzzles.censorly.lexical.match.rootbank import structures_of

        family = (('fam:рус²', 'sense:russian'),)
        for lemma in (
            'россия', 'российский', 'россиянин', 'россиянка',
            'русь', 'русский',
            'обрусеть', 'русин', 'русизм',
        ):
            with self.subTest(lemma=lemma):
                self.assertEqual(structures_of(lemma), family)

        for left, right in (
            ('Россия', 'русский'),
            ('России', 'русские'),
            ('России', 'россиянином'),
            ('русский', 'россиянка'),
            ('российский', 'обрусеть'),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(explain(left, right), 'root')
                self.assertEqual(explain(right, left), 'root')

        for word in ('русый', 'русачий', 'русалка'):
            with self.subTest(word=word):
                self.assertFalse(opens('Россия', word))
                self.assertFalse(opens(word, 'Россия'))

        for pair in (
            ('россия', 'русский'),
            ('российский', 'русский'),
            ('русь', 'русский'),
            ('россия', 'россиянин'),
            ('россия', 'россиянка'),
        ):
            self.assertNotIn(pair, PAIR_RELATIONS)

    def test_new_derivatives_do_not_intersect_clusters(self):
        from games.puzzles.censorly.lexical.match.proper_names import CLUSTERS

        clustered = {word for _name, words in CLUSTERS for word in words}
        for _source, derivative in WIKIDATA_GEOGRAPHIC_PAIRS:
            self.assertNotIn(derivative, clustered)

    def test_existing_matcher_results_remain_open(self):
        for left, right in (
            ('Алжира', 'алжирскому'),
            ('Пекином', 'пекинского'),
            ('Венгрии', 'венгром'),
            ('Чехии', 'чехом'),
            ('Ленина', 'ленинизма'),
        ):
            with self.subTest(left=left, right=right):
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))
