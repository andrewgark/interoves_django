import json

from django.test import SimpleTestCase, TestCase

from games.check import RaddleChecker
from games.models import Attempt, CHAIN_TASK_TYPES, Task
from games.raddle import (
    build_raddle_ui_context,
    clue_display_for_hint,
    default_raddle_state,
    input_size_for_mask,
    load_raddle_state,
    parse_length_mask,
    parse_raddle_data,
    playable_word_indices,
    raddle_word_solved_list,
    render_raddle_clue,
    resolve_assist_tiers,
    validate_raddle_checker_data,
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

    def test_parts(self):
        m = parse_length_mask('5-9')
        self.assertEqual(m['type'], 'parts')
        self.assertEqual(m['parts'], (5, 9))


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
        m2 = parse_length_mask('3-4')
        self.assertTrue(word_length_matches('НЬЮ-ЙОРК', m2))


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

    def test_mask_uses_squares(self):
        from games.raddle import length_mask_display
        self.assertEqual(length_mask_display(parse_length_mask(4)), '◼️◼️◼️◼️')
        self.assertEqual(length_mask_display(parse_length_mask('5-9')), '◼️◼️◼️◼️◼️-◼️◼️◼️◼️◼️◼️◼️◼️◼️')

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
        self.assertEqual(input_size_for_mask(parse_length_mask('5-9')), 30)

    def test_row_input_size_in_context(self):
        parsed = parse_raddle_data(_task())
        ctx = build_raddle_ui_context(parsed, default_raddle_state(13))
        # ПАРИЖАНИН (9) → size 18; САНКТ-ПЕТЕРБУРГ mask 5-9 → 15 slots → 30
        self.assertEqual(ctx['rows'][1]['input_size'], 18)
        self.assertEqual(ctx['rows'][1]['mask_slots'], 9)
        self.assertEqual(ctx['rows'][5]['input_size'], 30)
        self.assertEqual(ctx['rows'][5]['mask_slots'], 15)

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
