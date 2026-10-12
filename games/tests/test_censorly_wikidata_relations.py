"""Regression tests for the approved Wikidata geographic relations."""

from django.test import SimpleTestCase

from games.censorly.lexical.semantics import explain, opens


WIKIDATA_GEOGRAPHIC_PAIRS = (
    ('эфиопия', 'эфиоп'),
    ('эфиопия', 'эфиопка'),
    ('великобритания', 'британец'),
    ('великобритания', 'британка'),
    ('бразилия', 'бразилец'),
    ('бразилия', 'бразильянка'),
    ('россия', 'россиянин'),
    ('россия', 'россиянка'),
    ('канада', 'канадец'),
    ('канада', 'канадка'),
    ('германия', 'немец'),
    ('германия', 'немка'),
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
        from games.censorly.lexical.proper_names import PAIR_RELATIONS

        relations = set(PAIR_RELATIONS)
        self.assertEqual(len(WIKIDATA_GEOGRAPHIC_PAIRS), 37)
        for left, right in WIKIDATA_GEOGRAPHIC_PAIRS:
            with self.subTest(left=left, right=right):
                self.assertIn((left, right), relations)
                self.assertEqual(explain(left, right), 'proper')
                self.assertEqual(explain(right, left), 'proper')
                self.assertTrue(opens(left, right))
                self.assertTrue(opens(right, left))

    def test_rejected_pilot_pairs_are_not_added(self):
        from games.censorly.lexical.proper_names import PAIR_RELATIONS

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
            ('России', 'россиянином'),
            ('Германии', 'немкой'),
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
        from games.censorly.lexical.proper_names import PAIR_RELATIONS

        relations = set(PAIR_RELATIONS)
        self.assertIn(('кишинёв', 'кишинёвцы'), relations)
        self.assertNotIn(('кишинёв', 'кишинёвец'), relations)
        self.assertIn(('тель-авив', 'тель-авивец'), relations)
        self.assertNotIn(('тель', 'тель-авивец'), relations)

    def test_new_relations_do_not_create_cross_demonym_edges(self):
        by_source = {}
        for source, derivative in WIKIDATA_GEOGRAPHIC_PAIRS:
            by_source.setdefault(source, []).append(derivative)
        for source, derivatives in by_source.items():
            for index, left in enumerate(derivatives):
                for right in derivatives[index + 1:]:
                    # Германия already has an independent common-noun root
                    # family for немец/немка; all other approved pairs must
                    # remain explicit one-to-one edges.
                    if {left, right} == {'немец', 'немка'}:
                        continue
                    with self.subTest(source=source, left=left, right=right):
                        self.assertFalse(opens(left, right))
                        self.assertFalse(opens(right, left))

    def test_new_derivatives_do_not_intersect_clusters(self):
        from games.censorly.lexical.proper_names import CLUSTERS

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
