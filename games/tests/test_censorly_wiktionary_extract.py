"""Parser rules of the offline ru.wiktionary extractor."""

from __future__ import annotations

from django.test import SimpleTestCase

from games.puzzles.censorly.offline.tools.wiktionary.extract import (
    block_members,
    clean_word,
    homonym_sections,
    russian_part,
    segmentation,
    template_bodies,
)


class WiktionarySegmentationTests(SimpleTestCase):
    def test_morpheme_markers_map_to_tikhonov_kinds(self):
        self.assertEqual(segmentation('вид', 'вид|+∅|и=т'), ('вид:ROOT', 'т'))
        self.assertEqual(
            segmentation('помощь', 'по-|мощь|+∅'), ('по:PREF/мощь:ROOT', ''),
        )
        self.assertEqual(
            segmentation('треугольник', 'тр|-е-|уголь|-ник|+∅|и=т')[0],
            'тр:ROOT/е:LINK/уголь:ROOT/ник:SUFF',
        )

    def test_reflexive_postfix_and_j(self):
        self.assertEqual(
            segmentation('спутываться', 'с-|пут|-ыва|+ть|-ся|и=т')[0],
            'с:PREF/пут:ROOT/ыва:SUFF/ть:END/ся:POSTFIX',
        )
        self.assertEqual(
            segmentation('взаимодействие', 'взаим|-о-|дей|-ств|-иj|+е')[0],
            'взаим:ROOT/о:LINK/дей:ROOT/ств:SUFF/и(j):SUFF/е:END',
        )

    def test_orthographic_hyphens_are_kept(self):
        self.assertEqual(
            segmentation('по-русски', 'по-|рус|-ск|-и|и=т')[0],
            'по:PREF/-:HYPH/рус:ROOT/ск:SUFF/и:SUFF',
        )

    def test_prefixoids_and_suffixoids_are_bound_roots(self):
        self.assertEqual(
            segmentation('фотосинтетический', 'фото--|синтет|-ическ|+ий')[0],
            'фото:ROOT/синтет:ROOT/ическ:SUFF/ий:END',
        )
        self.assertEqual(
            segmentation('абсурдистан', 'абсурд|-и-|--стан|+∅')[0],
            'абсурд:ROOT/и:LINK/стан:ROOT',
        )

    def test_stress_marks_are_removed(self):
        self.assertEqual(clean_word('[[видение|ви́де́ние]]'), 'видение')
        self.assertEqual(clean_word('вида́вший'), 'видавший')


class WiktionaryPageTests(SimpleTestCase):
    PAGE = '\n'.join((
        '{{wikipedia}}',
        '= {{-ru-}} =',
        '== {{заголовок|ударение=коса́ (существительное I)}} ==',
        '=== Морфологические и синтаксические свойства ===',
        '{{сущ-ru|коса|ж 1d}}',
        '{{морфо-ru|кос|+а}}',
        '=== Родственные слова ===',
        '{{родств-блок',
        '|умласк=косичка',
        '|прилагательные=[[косный|ко́сный]], косой;<br />&#8226; длиннокосый',
        '}}',
        '== {{заголовок|ударение=коса́ (существительное II)}} ==',
        '{{морфо-ru|кос|+а}}',
        '= {{-uk-}} =',
        '{{морфо-ru|чужое|+а}}',
    ))

    def test_only_russian_part_and_homonyms(self):
        ru = russian_part(self.PAGE)
        self.assertNotIn('чужое', ru)
        sections = [header for header, text in homonym_sections(ru) if template_bodies(text, 'морфо-ru')]
        self.assertEqual(len(sections), 2)
        self.assertIn('существительное II', sections[1])

    def test_related_block_members(self):
        body = template_bodies(self.PAGE, 'родств-блок')[0]
        self.assertEqual(
            list(block_members(body)),
            [('умласк', 'косичка'), ('прилагательные', 'косный'),
             ('прилагательные', 'косой'), ('прилагательные', 'длиннокосый')],
        )

    def test_nest_template_alternants_and_fields(self):
        body = template_bodies(
            '{{родств-блок1|<!--x-->-вид⁽ʲ⁾-\n|имя шаблона=родств:вид\n'
            '|существительные=вид, [[видение|ви́де́ние]]\n|глаголы=видеть\n}}',
            'родств-блок1',
        )[0]
        self.assertEqual(
            list(block_members(body)),
            [('существительные', 'вид'), ('существительные', 'видение'), ('глаголы', 'видеть')],
        )
