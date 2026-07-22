import json

from django.test import SimpleTestCase, TestCase

from games.check import RaddleChecker
from games.models import Attempt, CHAIN_TASK_TYPES, Task
from games.raddle import (
    build_raddle_ui_context,
    clue_display_for_hint,
    default_raddle_state,
    input_size_for_mask,
    length_label_from_word,
    length_mask_display,
    load_raddle_state,
    mask_slot_count,
    parse_length_mask,
    parse_raddle_data,
    playable_word_indices,
    RADDLE_INPUT_FORMAT_SLOT,
    raddle_word_solved_list,
    render_raddle_clue,
    resolve_assist_tiers,
    validate_raddle_checker_data,
    raddle_input_format,
    raddle_word_core,
    raddle_word_is_latin,
    mixed_script_notice,
    word_length_matches,
    word_matches,
)


PARIS_LADDER = {
    'lengths': [5, 9, 7, 4, 6, '5-9', 9, 9, '3-4', 3, 6, 4, 5],
    'hints': [
        'Житель ____а',
        '____ без двух первых букв',
        'Там живёт ____',
        '"____ - ..." - песня 2000 года',
        'Откуда перенесли столицу в город ____',
        '____ включает остров Новая ...',
        'Столица страны ____',
        'Новый ____ теперь называется так',
        'Страна, в которой находится ____',
        'На флаге ____ можно найти 50 флагов ...',
        '____ без двух первых букв',
        'Столица страны к западу от ____',
    ],
    'words': [
        'ПАРИЖ', 'ПАРИЖАНИН', 'РИЖАНИН', 'РИГА', 'МОСКВА', 'САНКТ-ПЕТЕРБУРГ',
        'ГОЛЛАНДИЯ', 'АМСТЕРДАМ', 'НЬЮ-ЙОРК', 'США', 'СОМАЛИ', 'МАЛИ', 'ДАКАР',
    ],
}


def _task(**kwargs):
    defaults = {
        'task_type': 'raddle',
        'checker_data': json.dumps(PARIS_LADDER, ensure_ascii=False),
    }
    defaults.update(kwargs)
    return Task(**defaults)


class ParseLengthMaskTests(SimpleTestCase):
    def test_int_and_str(self):
        self.assertEqual(parse_length_mask(5)['type'], 'fixed')
        self.assertEqual(parse_length_mask('9')['length'], 9)

    def test_parts_hyphen(self):
        m = parse_length_mask('5-9')
        self.assertEqual(m['type'], 'parts')
        self.assertEqual(m['parts'], (5, 9))
        self.assertEqual(m['sep'], '-')
        self.assertEqual(m['label'], '5-9')

    def test_parts_space(self):
        m = parse_length_mask('5 3')
        self.assertEqual(m['type'], 'parts')
        self.assertEqual(m['parts'], (5, 3))
        self.assertEqual(m['sep'], ' ')
        self.assertEqual(m['label'], '5 3')


class LengthLabelFromWordTests(SimpleTestCase):
    def test_space_vs_hyphen(self):
        self.assertEqual(length_label_from_word('РОБИН ГУД'), '5 3')
        self.assertEqual(length_label_from_word('САНКТ-ПЕТЕРБУРГ'), '5-9')
        self.assertEqual(length_label_from_word('ПАРИЖ'), '5')

    def test_mask_display_follows_word_separator(self):
        self.assertEqual(
            length_mask_display(parse_length_mask('5-3'), 'РОБИН ГУД'),
            '◼️◼️◼️◼️◼️ ◼️◼️◼️',
        )
        self.assertEqual(
            length_mask_display(parse_length_mask('5 3')),
            '◼️◼️◼️◼️◼️ ◼️◼️◼️',
        )


class RaddleWordIsLatinTests(SimpleTestCase):
    def test_latin_and_cyrillic(self):
        self.assertTrue(raddle_word_is_latin('HELLO'))
        self.assertTrue(raddle_word_is_latin('NEW-YORK'))
        self.assertTrue(raddle_word_is_latin("O'KAY"))
        self.assertFalse(raddle_word_is_latin('ПАРИЖ'))
        self.assertFalse(raddle_word_is_latin('НЬЮ-ЙОРК'))
        self.assertFalse(raddle_word_is_latin('HELLO ПРИВЕТ'))
        self.assertFalse(raddle_word_is_latin('123'))
        self.assertFalse(raddle_word_is_latin(''))

    def test_mixed_script_notice_forms(self):
        self.assertEqual(
            mixed_script_notice(1),
            '1 слово должно быть написано на латинице, остальное на кириллице',
        )
        self.assertEqual(
            mixed_script_notice(2),
            '2 слова должны быть написаны на латинице, остальное на кириллице',
        )
        self.assertEqual(
            mixed_script_notice(5),
            '5 слов должны быть написаны на латинице, остальное на кириллице',
        )
        self.assertEqual(mixed_script_notice(0), '')


class ParseRaddleDataTests(SimpleTestCase):
    def test_full_json(self):
        p = parse_raddle_data(_task())
        self.assertIsNotNone(p)
        self.assertEqual(p['n_words'], 13)
        self.assertEqual(len(p['hints']), 12)

    def test_words_from_answer(self):
        data = dict(PARIS_LADDER)
        words = data.pop('words')
        p = parse_raddle_data(_task(
            checker_data=json.dumps(data, ensure_ascii=False),
            answer='\n'.join(words),
        ))
        self.assertEqual(p['words'][0], 'ПАРИЖ')


class PlayableIndicesTests(SimpleTestCase):
    def test_initial_playable(self):
        st = default_raddle_state(13)
        playable = playable_word_indices(st, 13)
        self.assertEqual(playable, {1, 11})

    def test_after_left_solve(self):
        st = {'solved_indices': [0, 1, 12], 'used_hints': [0], 'total': 1}
        playable = playable_word_indices(st, 13)
        self.assertEqual(playable, {2, 11})


class WordLengthTests(SimpleTestCase):
    def test_hyphenated(self):
        m = parse_length_mask('5-9')
        self.assertTrue(word_length_matches('САНКТ-ПЕТЕРБУРГ', m))
        self.assertTrue(word_length_matches('САНКТПЕТЕРБУРГ', m))
        m2 = parse_length_mask('3-4')
        self.assertTrue(word_length_matches('НЬЮ-ЙОРК', m2))
        self.assertTrue(word_length_matches('НЬЮЙОРК', m2))

    def test_word_core_strips_punctuation(self):
        self.assertEqual(raddle_word_core('САНКТ-ПЕТЕРБУРГ'), 'санктпетербург')
        self.assertEqual(raddle_word_core('НЬЮ ЙОРК'), 'ньюйорк')

    def test_input_format_from_word(self):
        self.assertEqual(raddle_input_format('САНКТ-ПЕТЕРБУРГ'), '#####' + '-' + '#########')
        self.assertEqual(raddle_input_format('ПАРИЖАНИН'), '#' * 9)
        self.assertEqual(
            raddle_input_format('НЬЮ-ЙОРК'),
            '###' + '-' + '####',
        )

    def test_input_format_from_mask_only(self):
        self.assertEqual(raddle_input_format(mask=parse_length_mask('5-9')), '#####' + '-' + '#########')
        self.assertEqual(raddle_input_format(mask=parse_length_mask('5 3')), '##### ###')

    def test_word_matches_without_separators(self):
        self.assertTrue(word_matches('САНКТПЕТЕРБУРГ', ['САНКТ-ПЕТЕРБУРГ']))
        self.assertTrue(word_matches('НЬЮЙОРК', ['НЬЮ-ЙОРК']))


class RaddleInputEdgeTests(SimpleTestCase):
    def test_format_space_comma_apostrophe(self):
        self.assertEqual(raddle_input_format('НЬЮ ЙОРК'), '### ####')
        self.assertEqual(raddle_input_format('А,Б'), '#,#')
        self.assertEqual(raddle_input_format("О'КЕЙ"), "#'###")

    def test_format_multiple_hyphens(self):
        fmt = raddle_input_format('АБВ-ГДЕ-ЖЗИ')
        self.assertEqual(fmt, '###-###-###')
        self.assertEqual(fmt.count(RADDLE_INPUT_FORMAT_SLOT), 9)

    def test_format_slots_match_mask_slots_for_paris_ladder(self):
        for word, length_raw in zip(PARIS_LADDER['words'], PARIS_LADDER['lengths']):
            mask = parse_length_mask(length_raw)
            fmt = raddle_input_format(word, mask)
            self.assertEqual(
                fmt.count(RADDLE_INPUT_FORMAT_SLOT),
                mask_slot_count(mask, word),
                msg=word,
            )

    def test_yo_matches_e_on_server(self):
        self.assertTrue(word_matches('ЕЛКА', ['ЁЛКА']))
        self.assertTrue(word_matches('ЁЛКА', ['ЕЛКА']))

    def test_word_core_ignores_nbsp_and_quotes(self):
        self.assertEqual(raddle_word_core('«САНКТ»\u00a0—\u00a0ПЕТЕРБУРГ'), 'санктпетербург')

    def test_length_mismatch_rejects_extra_letters(self):
        m = parse_length_mask(5)
        self.assertFalse(word_length_matches('АБВГДЕ', m))
        self.assertTrue(word_length_matches('АБВГ-Д', m))

    def test_alternative_answer_without_punctuation(self):
        self.assertTrue(word_matches('НЬЮЙОРК', ['НЬЮ-ЙОРК', 'НЬЮ ЙОРК']))

    def test_checker_accepts_letters_only_hyphenated(self):
        state = json.dumps({
            'solved_indices': [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            'used_hints': [],
            'assist_tier': {},
            'total': 0.0,
        })
        ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)
        r = ch.check(
            json.dumps({'word_index': 5, 'word': 'САНКТПЕТЕРБУРГ'}),
            Attempt(
                text=json.dumps({'word_index': 5, 'word': 'САНКТПЕТЕРБУРГ'}),
                task=_task(),
            ),
        )
        self.assertIn(r.status, ('Partial', 'Ok'))
        self.assertIn(5, json.loads(r.state)['solved_indices'])

    def test_checker_rejects_wrong_length_letters_only(self):
        ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False))
        r = ch.check(
            json.dumps({'word_index': 1, 'word': 'ПАРИЖАНИ'}),
            Attempt(
                text=json.dumps({'word_index': 1, 'word': 'ПАРИЖАНИ'}),
                task=_task(),
            ),
        )
        self.assertEqual(r.status, 'Wrong')
        self.assertIn('длина', (r.comment or '').lower())

    def test_checker_rejects_punctuation_only_submission(self):
        ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False))
        r = ch.check(
            json.dumps({'word_index': 1, 'word': '---'}),
            Attempt(
                text=json.dumps({'word_index': 1, 'word': '---'}),
                task=_task(),
            ),
        )
        self.assertEqual(r.status, 'Wrong')

    def test_ui_context_input_format_on_all_rows(self):
        parsed = parse_raddle_data(_task())
        ctx = build_raddle_ui_context(parsed, default_raddle_state(13))
        for row in ctx['rows']:
            self.assertTrue(row['input_format'])
            self.assertGreater(row['input_format_len'], 0)
            self.assertGreaterEqual(
                row['input_format_len'],
                row['mask_slots'],
            )

    def test_format_prefers_canonical_over_mask(self):
        word = 'НЬЮ ЙОРК'
        mask = parse_length_mask(7)
        self.assertEqual(raddle_input_format(word, mask), '### ####')
        self.assertEqual(raddle_input_format(mask=mask), '#' * 7)

    def test_server_accepts_digits_in_answer(self):
        self.assertTrue(word_matches('ТОП3', ['ТОП3']))
        self.assertEqual(raddle_word_core('ТОП-3'), 'топ3')


class RaddleCheckerTests(SimpleTestCase):
    def _checker(self, state=None):
        return RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)

    def _attempt(self, word_index, word):
        t = _task()
        a = Attempt(text=json.dumps({'word_index': word_index, 'word': word}), task=t)
        return a

    def test_correct_first_open_word(self):
        ch = self._checker()
        r = ch.check(
            json.dumps({'word_index': 1, 'word': 'ПАРИЖАНИН'}),
            self._attempt(1, 'ПАРИЖАНИН'),
        )
        self.assertEqual(r.status, 'Partial')
        st = json.loads(r.state)
        self.assertIn(1, st['solved_indices'])
        self.assertIn(0, st['used_hints'])

    def test_correct_hyphenated_without_separator(self):
        state = json.dumps({
            'solved_indices': [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12],
            'used_hints': [],
            'assist_tier': {},
            'total': 0.0,
        })
        ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)
        r = ch.check(
            json.dumps({'word_index': 5, 'word': 'САНКТПЕТЕРБУРГ'}),
            self._attempt(5, 'САНКТПЕТЕРБУРГ'),
        )
        self.assertIn(r.status, ('Partial', 'Ok'))
        st = json.loads(r.state)
        self.assertIn(5, st['solved_indices'])

    def test_correct_bottom_open_word_uses_last_hint(self):
        """Нижнее слово: переход к последнему данному → последняя подсказка (не предпоследняя)."""
        ch = self._checker()
        # PARIS: 13 слов, нижнее playable = 11 (ДАКАР=12 дан)
        r = ch.check(
            json.dumps({'word_index': 11, 'word': 'МАЛИ'}),
            self._attempt(11, 'МАЛИ'),
        )
        self.assertEqual(r.status, 'Partial')
        st = json.loads(r.state)
        self.assertIn(11, st['solved_indices'])
        self.assertIn(11, st['used_hints'])  # hints[11]: МАЛИ → ДАКАР
        self.assertNotIn(10, st['used_hints'])

    def test_already_solved_word(self):
        state = json.dumps({
            'solved_indices': [0, 1, 12],
            'used_hints': [0],
            'assist_tier': {},
            'total': 1.0,
        })
        ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)
        r = ch.check(
            json.dumps({'word_index': 1, 'word': 'ПАРИЖАНИН'}),
            self._attempt(1, 'ПАРИЖАНИН'),
        )
        self.assertEqual(r.status, 'Wrong')
        self.assertIn('уже решено', (r.comment or '').lower())

    def test_wrong_word(self):
        ch = self._checker()
        r = ch.check(
            json.dumps({'word_index': 1, 'word': 'ЛОНДОН'}),
            self._attempt(1, 'ЛОНДОН'),
        )
        self.assertEqual(r.status, 'Wrong')
        st = json.loads(r.state)
        self.assertNotIn(1, st['solved_indices'])

    def test_not_playable_index(self):
        ch = self._checker()
        r = ch.check(
            json.dumps({'word_index': 5, 'word': 'САНКТ-ПЕТЕРБУРГ'}),
            self._attempt(5, 'САНКТ-ПЕТЕРБУРГ'),
        )
        self.assertEqual(r.status, 'Wrong')
        self.assertIn('можно сдавать', (r.comment or '').lower())

    def test_complete_ladder(self):
        ch = self._checker()
        state = None
        order = [1, 11, 2, 10, 3, 9, 4, 8, 5, 7, 6]
        for idx in order:
            word = PARIS_LADDER['words'][idx]
            ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)
            r = ch.check(
                json.dumps({'word_index': idx, 'word': word}),
                self._attempt(idx, word),
            )
            state = r.state
        self.assertEqual(r.status, 'Ok')

    def test_complete_ladder_letters_only(self):
        from games.raddle import raddle_word_core

        ch = self._checker()
        state = None
        order = [1, 11, 2, 10, 3, 9, 4, 8, 5, 7, 6]
        for idx in order:
            word = raddle_word_core(PARIS_LADDER['words'][idx]).upper()
            ch = RaddleChecker(json.dumps(PARIS_LADDER, ensure_ascii=False), state)
            r = ch.check(
                json.dumps({'word_index': idx, 'word': word}),
                self._attempt(idx, word),
            )
            state = r.state
        self.assertEqual(r.status, 'Ok')

    def test_assist_scoring_fractions(self):
        from decimal import Decimal
        state = json.dumps({
            'solved_indices': [0, 12],
            'used_hints': [],
            'assist_tier': {'1': 1},
            'total': 0.0,
        })
        ch = self._checker(state)
        r = ch.check(
            json.dumps({'word_index': 1, 'word': 'ПАРИЖАНИН'}),
            self._attempt(1, 'ПАРИЖАНИН'),
        )
        st = json.loads(r.state)
        self.assertAlmostEqual(st['total'], 0.5)
        self.assertIsInstance(r.points, Decimal)
        # Must multiply with task.get_points() (DecimalField) without TypeError.
        self.assertEqual(r.points * Decimal('1'), Decimal('0.5'))


class RaddleUiContextTests(SimpleTestCase):
    def test_unused_hints_sorted(self):
        parsed = parse_raddle_data(_task())
        ctx = build_raddle_ui_context(parsed, default_raddle_state(13))
        texts = [h['display'] for h in ctx['unused_hints']]
        self.assertEqual(texts, sorted(texts, key=str.lower))

    def test_clue_prev_placeholder(self):
        from games.raddle import render_transition_clue
        # {prev} и ____ — один blank; после подстановки не дублируем слово
        self.assertEqual(render_raddle_clue('{prev} ________', 'BRICK', True), 'BRICK')
        self.assertEqual(render_raddle_clue('{word} test', 'BRICK', False), '____ test')
        self.assertEqual(render_raddle_clue('Житель ____а', 'ПАРИЖ', True), 'Житель ПАРИЖа')
        # нет слота следующего → стрелка
        self.assertEqual(
            render_transition_clue(
                '____ рядом.', prev_word='КОНЬ', next_word='СЛОН',
                prev_known=True, next_known=True,
            ),
            'КОНЬ рядом → СЛОН',
        )
        # есть ... как слот следующего
        self.assertEqual(
            render_transition_clue(
                '"____ - ..." - песня',
                prev_word='РИГА', next_word='МОСКВА',
                prev_known=True, next_known=True,
            ),
            '"РИГА - МОСКВА" - песня',
        )
        # любое ... — слот следующего (подставляем во все вхождения)
        self.assertEqual(
            render_transition_clue(
                '... Тьмы - это ____',
                prev_word='САТАНА', next_word='КНЯЗЬ',
                prev_known=True, next_known=True,
            ),
            'КНЯЗЬ Тьмы - это САТАНА',
        )

    def test_clue_display_for_hint(self):
        parsed = parse_raddle_data(_task())
        solved = {0, 12}
        # без фокуса не спойлерим отгаданные слова в unused
        self.assertIn('____', clue_display_for_hint('{prev} → ?', 0, parsed['words'], solved))
        self.assertIn('____', clue_display_for_hint('{prev} → ?', 1, parsed['words'], solved))
        # фокус сверху (prev): во все ____
        self.assertEqual(
            clue_display_for_hint(
                '____ без букв', 5, parsed['words'], set(),
                focus_ref_word='ПАРИЖ', focus_ref_role='prev',
            ),
            'ПАРИЖ без букв',
        )
        # фокус снизу (next): в ... / →
        self.assertEqual(
            clue_display_for_hint(
                '____ рядом.', 5, parsed['words'], set(),
                focus_ref_word='ДАКАР', focus_ref_role='next',
            ),
            '____ рядом → ДАКАР',
        )
        self.assertEqual(
            clue_display_for_hint(
                '"____ - ..."', 5, parsed['words'], set(),
                focus_ref_word='ДАКАР', focus_ref_role='next',
            ),
            '"____ - ДАКАР"',
        )

    def test_used_clue_next_highlighted_green(self):
        from games.raddle import used_clue_display
        html = used_clue_display('____ рядом.', 0, ['КОНЬ', 'СЛОН'], html=True)
        self.assertIn('new-raddle-clue-next', str(html))
        self.assertIn('СЛОН', str(html))
        self.assertIn('КОНЬ', str(html))

    def test_all_rows_visible_no_collapse(self):
        parsed = parse_raddle_data(_task())
        state = {'solved_indices': [0, 1, 12], 'used_hints': [0], 'total': 1}
        ctx = build_raddle_ui_context(parsed, state)
        self.assertFalse(any(r['compact_hidden'] for r in ctx['rows']))
        self.assertEqual(ctx['collapsed_solved_count'], 0)

    def test_used_hint_display_shows_arrow_to_next(self):
        from games.raddle import used_clue_display
        parsed = parse_raddle_data(_task())
        state = {'solved_indices': [0, 1, 12], 'used_hints': [0], 'total': 1}
        ctx = build_raddle_ui_context(parsed, state)
        self.assertEqual(len(ctx['used_hints']), 1)
        self.assertEqual(ctx['used_hints'][0]['index'], 0)
        self.assertEqual(
            ctx['used_hints'][0]['display'],
            used_clue_display(PARIS_LADDER['hints'][0], 0, parsed['words']),
        )
        self.assertIn('→', ctx['used_hints'][0]['display'])
        self.assertIn('ПАРИЖАНИН', ctx['used_hints'][0]['display'])

    def test_last_word_shows_two_clue_layouts(self):
        """Одно оставшееся слово: два разных варианта привязки подсказок."""
        parsed = parse_raddle_data(_task())
        # Осталось слово index=6 (ГОЛЛАНДИЯ); соседи 5 и 7 решены.
        solved = list(range(0, 6)) + list(range(7, 13))
        used = list(range(0, 5)) + list(range(7, 12))
        state = {
            'solved_indices': solved,
            'used_hints': used,
            'total': 10,
        }
        ctx = build_raddle_ui_context(parsed, state)
        self.assertTrue(ctx['last_word_dual_clues'])
        self.assertEqual(ctx['default_focus_index'], 6)
        self.assertEqual(ctx['unused_hints'], [])
        options = ctx['last_word_clue_options']
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['id'], 'ab-bc')
        self.assertEqual(options[1]['id'], 'bc-ab')
        # Один и тот же порядок текстов, разная привязка переходов.
        self.assertEqual([h['index'] for h in options[0]['hints']], [5, 6])
        self.assertEqual([h['index'] for h in options[1]['hints']], [5, 6])
        self.assertEqual([h['pair'] for h in options[0]['hints']], ['ab', 'bc'])
        self.assertEqual([h['pair'] for h in options[1]['hints']], ['bc', 'ab'])
        before_word = parsed['words'][5]  # САНКТ-ПЕТЕРБУРГ
        focus_word = parsed['words'][6]   # ГОЛЛАНДИЯ
        after_word = parsed['words'][7]   # АМСТЕРДАМ
        hint_ab = parsed['hints'][5]
        hint_bc = parsed['hints'][6]
        self.assertNotEqual(hint_ab, hint_bc)
        opt1_first = str(options[0]['hints'][0]['display_html'])
        opt1_second = str(options[0]['hints'][1]['display_html'])
        opt2_first = str(options[1]['hints'][0]['display_html'])
        opt2_second = str(options[1]['hints'][1]['display_html'])
        self.assertNotEqual(opt1_first, opt2_first)
        self.assertNotEqual(opt1_second, opt2_second)
        self.assertIn(before_word, opt1_first)
        self.assertIn(after_word, opt1_second)
        self.assertIn(after_word, opt2_first)
        self.assertIn(before_word, opt2_second)
        for html in (opt1_first, opt1_second, opt2_first, opt2_second):
            self.assertNotIn(focus_word, html)
            self.assertNotIn('new-raddle-clue-focus', html)
        self.assertIn('new-raddle-clue-before', opt1_first)
        self.assertIn('new-raddle-clue-after', opt1_second)
        self.assertIn('new-raddle-clue-after', opt2_first)
        self.assertIn('new-raddle-clue-before', opt2_second)
        row_before = next(r for r in ctx['rows'] if r['index'] == 5)
        row_focus = next(r for r in ctx['rows'] if r['index'] == 6)
        row_after = next(r for r in ctx['rows'] if r['index'] == 7)
        self.assertEqual(row_before['last_word_role'], 'before')
        self.assertEqual(row_focus['last_word_role'], 'focus')
        self.assertEqual(row_after['last_word_role'], 'after')

    def test_last_word_bard_example(self):
        """Регрессия: варианты должны отличаться, Б не подставляется."""
        from games.raddle import build_last_word_clue_options
        data = {
            'lengths': [5, 4, 6, 7],
            'hints': [
                'не используется',
                'Главный атрибут ____а',
                '____ — это то, на чём можно сделать этот приём',
            ],
            'words': ['СТАРТ', 'БАРД', 'ГИТАРА', 'ПЕРЕБОР'],
            'raddle_assist': {'enabled': True, 'fractions': [1, 0.5, 0]},
        }
        parsed = parse_raddle_data(_task(checker_data=json.dumps(data, ensure_ascii=False)))
        options = build_last_word_clue_options(parsed, 2, revealed_clue_indices=set())
        opt1 = [str(h['display_html']) for h in options[0]['hints']]
        opt2 = [str(h['display_html']) for h in options[1]['hints']]
        self.assertNotEqual(opt1[0], opt2[0])
        self.assertNotEqual(opt1[1], opt2[1])
        self.assertIn('БАРД', opt1[0])
        self.assertIn('ПЕРЕБОР', opt1[1])
        self.assertIn('ПЕРЕБОР', opt2[0])
        self.assertIn('БАРД', opt2[1])
        for line in opt1 + opt2:
            self.assertNotIn('ГИТАРА', line)

    def test_mask_uses_squares(self):
        self.assertEqual(length_mask_display(parse_length_mask(4)), '◼️◼️◼️◼️')
        self.assertEqual(length_mask_display(parse_length_mask('5-9')), '◼️◼️◼️◼️◼️-◼️◼️◼️◼️◼️◼️◼️◼️◼️')

    def test_spaced_word_length_label_not_hyphen(self):
        """Даже если в lengths ошибочно «5-3», UI показывает «5 3» по пробелу в слове."""
        data = {
            'lengths': [5, '5-3', 4],
            'hints': ['a', 'b'],
            'words': ['СТАРТ', 'РОБИН ГУД', 'ФИНИ'],
            'raddle_assist': {'enabled': False, 'fractions': [1, 0.5, 0]},
        }
        parsed = parse_raddle_data(_task(checker_data=json.dumps(data, ensure_ascii=False)))
        ctx = build_raddle_ui_context(parsed, default_raddle_state(3))
        row = ctx['rows'][1]
        self.assertEqual(row['length_label'], '5 3')
        self.assertEqual(row['input_format'], '##### ###')
        self.assertFalse(row['is_latin'])
        self.assertIn(' ', row['mask_html'])
        self.assertNotIn('-', row['mask_html'])

    def test_latin_word_marks_is_latin(self):
        data = {
            'lengths': [5, 5, 5],
            'hints': ['a', 'b'],
            'words': ['START', 'HELLO', 'FINISH'],
            'raddle_assist': {'enabled': False, 'fractions': [1, 0.5, 0]},
        }
        parsed = parse_raddle_data(_task(checker_data=json.dumps(data, ensure_ascii=False)))
        ctx = build_raddle_ui_context(parsed, default_raddle_state(3))
        self.assertTrue(ctx['rows'][0]['is_latin'])
        self.assertTrue(ctx['rows'][0]['show_latin_flag'])
        self.assertEqual(ctx['rows'][0]['input_script'], 'latin')
        self.assertTrue(ctx['rows'][1]['is_latin'])
        self.assertEqual(ctx['rows'][1]['length_label'], '5')
        self.assertFalse(ctx['mixed_script'])
        self.assertEqual(ctx['mixed_script_notice'], '')

    def test_mixed_script_hides_flag_and_allows_both(self):
        data = {
            'lengths': [5, 5, 5],
            'hints': ['a', 'b'],
            'words': ['СТАРТ', 'HELLO', 'ФИНИШ'],
            'mixed_script': True,
            'raddle_assist': {'enabled': False, 'fractions': [1, 0.5, 0]},
        }
        parsed = parse_raddle_data(_task(checker_data=json.dumps(data, ensure_ascii=False)))
        self.assertTrue(parsed['mixed_script'])
        ctx = build_raddle_ui_context(parsed, default_raddle_state(3))
        self.assertTrue(ctx['mixed_script'])
        self.assertEqual(ctx['latin_word_count'], 1)
        self.assertEqual(
            ctx['mixed_script_notice'],
            '1 слово должно быть написано на латинице, остальное на кириллице',
        )
        self.assertFalse(ctx['rows'][0]['is_latin'])
        self.assertFalse(ctx['rows'][0]['show_latin_flag'])
        self.assertEqual(ctx['rows'][0]['input_script'], 'mixed')
        self.assertTrue(ctx['rows'][1]['is_latin'])
        self.assertFalse(ctx['rows'][1]['show_latin_flag'])
        self.assertEqual(ctx['rows'][1]['input_script'], 'mixed')

    def test_attempts_exhausted_in_tournament(self):
        parsed = parse_raddle_data(_task())
        state = default_raddle_state(13)
        attempts = []
        for _ in range(3):
            attempts.append(type('A', (), {
                'text': json.dumps({'word_index': 1, 'word': 'X'}),
            })())
        ctx = build_raddle_ui_context(
            parsed, state, attempts, max_attempts=3, mode='tournament',
        )
        row1 = ctx['rows'][1]
        self.assertTrue(row1['attempts_exhausted'])
        self.assertFalse(row1['is_playable'])

    def test_attempts_not_exhausted_in_general(self):
        parsed = parse_raddle_data(_task())
        state = default_raddle_state(13)
        attempts = []
        for _ in range(10):
            attempts.append(type('A', (), {
                'text': json.dumps({'word_index': 1, 'word': 'X'}),
            })())
        ctx = build_raddle_ui_context(
            parsed, state, attempts, max_attempts=3, mode='general',
        )
        self.assertTrue(ctx['rows'][1]['is_playable'])
        self.assertFalse(ctx['rows'][1]['attempts_exhausted'])

    def test_input_size_from_mask(self):
        # size с запасом: плейсхолдер ◼️ шире буквы
        self.assertEqual(input_size_for_mask(parse_length_mask(4)), 8)
        self.assertEqual(input_size_for_mask(parse_length_mask(5)), 10)
        self.assertEqual(input_size_for_mask(parse_length_mask(9)), 18)
        self.assertEqual(input_size_for_mask(parse_length_mask('5-9')), 28)

    def test_row_input_size_in_context(self):
        parsed = parse_raddle_data(_task())
        ctx = build_raddle_ui_context(parsed, default_raddle_state(13))
        # ПАРИЖАНИН (9) → size 18; САНКТ-ПЕТЕРБУРГ mask 5-9 → 14 букв, формат 15 символов
        self.assertEqual(ctx['rows'][1]['input_size'], 18)
        self.assertEqual(ctx['rows'][1]['mask_slots'], 9)
        self.assertEqual(ctx['rows'][1]['input_format'], '#' * 9)
        self.assertEqual(ctx['rows'][1]['input_format_len'], 9)
        self.assertEqual(ctx['rows'][5]['input_size'], 28)
        self.assertEqual(ctx['rows'][5]['mask_slots'], 14)
        self.assertEqual(ctx['rows'][5]['input_format'], '#####' + '-' + '#########')
        self.assertEqual(ctx['rows'][5]['input_format_len'], 15)

    def test_word_solved_list_from_state(self):
        parsed = parse_raddle_data(_task())
        attempts = [
            type('A', (), {
                'state': json.dumps({'solved_indices': [0, 1, 12], 'used_hints': [0], 'total': 1}),
            })(),
        ]
        flags = raddle_word_solved_list(parsed, attempts)
        self.assertTrue(flags[0])
        self.assertTrue(flags[1])
        self.assertFalse(flags[2])
        self.assertTrue(flags[12])

    def test_word_solved_list_default_endpoints(self):
        parsed = parse_raddle_data(_task())
        flags = raddle_word_solved_list(parsed, [])
        self.assertTrue(flags[0])
        self.assertTrue(flags[12])
        self.assertFalse(flags[1])

    def test_assist_tier_from_hint_desc(self):
        parsed = parse_raddle_data(_task())
        state = default_raddle_state(13)

        class _Hint:
            desc = 'raddle_clue:3'

        class _HA:
            is_real_request = True
            hint = _Hint()

        tiers = resolve_assist_tiers(state, [_HA()])
        self.assertEqual(tiers.get(3), 1)

    def test_result_squares_all_green_when_no_assist(self):
        from games.raddle import raddle_result_squares

        parsed = parse_raddle_data(_task())
        state = {
            'solved_indices': list(range(13)),
            'used_hints': [],
            'assist_tier': {},
            'total': 11.0,
        }
        squares = raddle_result_squares(parsed, state)
        self.assertEqual(squares, '🟩' * 11)

    def test_result_squares_mixed_tiers(self):
        from games.raddle import raddle_result_squares

        parsed = parse_raddle_data(_task())
        state = {
            'solved_indices': list(range(13)),
            'used_hints': [],
            'assist_tier': {'1': 2, '2': 1},
            'total': 9.0,
        }
        squares = raddle_result_squares(parsed, state)
        self.assertTrue(squares.startswith('🟥🟨'))
        self.assertEqual(len(squares), 11)

    def test_result_squares_partial_uses_white(self):
        from games.raddle import raddle_result_squares

        parsed = parse_raddle_data(_task())
        # endpoints + first two middle words
        state = {
            'solved_indices': [0, 1, 2, 12],
            'used_hints': [],
            'assist_tier': {'1': 0, '2': 1},
            'total': 1.5,
        }
        self.assertEqual(raddle_result_squares(parsed, state), '')
        squares = raddle_result_squares(parsed, state, allow_partial=True)
        self.assertTrue(squares.startswith('🟩🟨'))
        self.assertEqual(squares.count('⬜'), 9)
        self.assertEqual(len(squares), 11)

    def test_result_squares_partial_empty_without_middle_progress(self):
        from games.raddle import raddle_result_squares

        parsed = parse_raddle_data(_task())
        state = {
            'solved_indices': [0, 12],
            'used_hints': [],
            'assist_tier': {},
            'total': 0.0,
        }
        self.assertEqual(raddle_result_squares(parsed, state, allow_partial=True), '')

    def test_validate_checker_data_ok(self):
        self.assertEqual(validate_raddle_checker_data(json.dumps(PARIS_LADDER, ensure_ascii=False)), [])

    def test_validate_checker_data_bad_hints_count(self):
        bad = dict(PARIS_LADDER)
        bad['hints'] = bad['hints'][:2]
        errs = validate_raddle_checker_data(json.dumps(bad, ensure_ascii=False))
        self.assertTrue(any('на 1 меньше' in e or 'hints' in e for e in errs))

    def test_validate_checker_data_empty_hint(self):
        bad = dict(PARIS_LADDER)
        bad['hints'] = list(bad['hints'])
        bad['hints'][3] = '  '
        errs = validate_raddle_checker_data(json.dumps(bad, ensure_ascii=False))
        self.assertTrue(any('пустая' in e for e in errs))


class ChainTaskTypeTests(SimpleTestCase):
    def test_raddle_in_chain_types(self):
        self.assertIn('raddle', CHAIN_TASK_TYPES)


class HintNumberSortTests(SimpleTestCase):
    def test_number_key_sorts_by_segments(self):
        from games.models import Hint

        self.assertEqual(Hint.number_key('1.10'), (1, 10))
        self.assertLess(Hint.number_key('1.2'), Hint.number_key('1.10'))
        self.assertLess(Hint.number_key('1.9'), Hint.number_key('2.1'))


class EnsureRaddleAssistHintsTests(TestCase):
    def test_creates_dotted_hint_numbers(self):
        from games.models import CheckerType, Hint, TaskGroup
        from games.raddle import ensure_raddle_assist_hints

        CheckerType.objects.get_or_create(pk='raddle')
        tg = TaskGroup.objects.create(label='tg-raddle-hints')
        task = _task(task_group=tg)
        task.save()

        created = ensure_raddle_assist_hints(task)
        self.assertGreater(created, 0)

        hints = {h.desc: h.number for h in Hint.objects.filter(task=task)}
        self.assertEqual(hints.get('raddle_clue:1'), '1.1')
        self.assertEqual(hints.get('raddle_clue:2'), '1.2')
        self.assertEqual(hints.get('raddle_answer:1'), '2.1')
        self.assertEqual(hints.get('raddle_answer:10'), '2.10')
