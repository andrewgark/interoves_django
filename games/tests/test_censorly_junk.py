"""Wiki service marks and parser leftovers (``article.junk``)."""

from django.test import SimpleTestCase

from games.puzzles.censorly.article.junk import JUNK_PATTERNS, find_junk
from games.puzzles.censorly.text.tokenize import FORMULA_END, FORMULA_START

F0, F1 = FORMULA_START, FORMULA_END

# One text per pattern, each the way the parser once let it through.
_SAMPLES = {
    'script_help_mark': 'Жёлтое море\n\nкор. 황해?, 黃海? хванхэ.',
    'wikidata_more_sources': 'Мбаппе\n\nрод. 20 декабря 1998[…], Париж.',
    'editorial_note': 'Алиса\n\nКнига считается[кем?] лучшей.',
    'dead_link_note': 'Михаил\n\nангелов (недоступная ссылка).',
    'checked_on': 'Ботулизм\n\nтиндализацию Проверено 27 марта 2026. Токсин.',
    'zero_width_space': 'Пастер\n\nLouis Pasteur​; 1822.',
    'negation_overlay': f'Анализ\n\nпри условии {F0}a = ̸0{F1}.',
    'coordinate_duplicate': 'Феникс\n\n68°13′ с. ш. 125°44′ з. д. / 68,2188°.',
    'empty_script': f'Торий\n\n{F0}₍₎²³²Th{F1}.',
    'degree_ring': f'Винт\n\n{F0}β = 30^(∘){F1}.',
    'tex_command_word': f'Функция\n\n{F0}fcolon X → Y{F1}.',
    'lead_separator': 'Берли́н (нем. Berlin,) — столица.\n\nТекст.',
}


class JunkPatternTests(SimpleTestCase):
    def test_every_pattern_has_a_sample_that_it_finds(self):
        self.assertEqual(set(_SAMPLES), {pattern.name for pattern in JUNK_PATTERNS})
        for name, text in _SAMPLES.items():
            with self.subTest(name):
                self.assertEqual({hit.pattern for hit in find_junk(text)}, {name})

    def test_ordinary_text_is_not_junk(self):
        text = (
            'Вольтер (фр. Voltaire) — писатель.\n\n'
            '«Whatever is, is right»; right- и left-handed materials. '
            'Смайлик «Весёлые ребята;)», формула ' + F0 + 'A(i,) = (aᵢ₁ aᵢ₂){' + F1 + '}, '
            'левая скобка (, правая скобка). Цитата: «Я […] играю». Как? Никак.'
        )
        self.assertEqual(find_junk(text), [])

    def test_code_listings_are_skipped(self):
        from games.puzzles.censorly.text.tokenize import CODE_END, CODE_START

        self.assertEqual(find_junk(f'Код\n\n{CODE_START}x = [кем?]​{CODE_END}'), [])

    def test_fixture_articles_are_clean(self):
        from games.puzzles.censorly.offline.fixtures import iter_fixtures

        hits = {
            name: [(hit.pattern, hit.context) for hit in find_junk(body)]
            for name, _kind, body in iter_fixtures()
        }
        self.assertEqual({name: found for name, found in hits.items() if found}, {})
