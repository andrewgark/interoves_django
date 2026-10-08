"""Gameplay semantics: every possible reading, full root-set equality."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from games.censorly.lexical.core import fold
from games.censorly.lexical.semantics import explain, initially_open, opens

TRUE_CASES = (
    ('пчела', 'пчёлами', 'lexeme'),
    ('улей', 'улья', 'lexeme'),
    ('улей', 'ульями', 'lexeme'),
    ('белка', 'белки', 'lexeme'),
    ('белок', 'белки', 'lexeme'),
    ('Белка', 'белки', 'lexeme'),
    ('БЕЛКА', 'белки', 'lexeme'),
    ('прибыть', 'прибыли', 'lexeme'),
    ('прибыль', 'прибыли', 'lexeme'),
    ('ходить', 'выходить', 'root'),
    ('ходить', 'приходить', 'root'),
    ('писать', 'написать', 'root'),
    ('мыть', 'мыться', 'root'),
    ('открыть', 'открыться', 'root'),
    ('работа', 'работать', 'root'),
    ('работа', 'рабочий', 'root'),
    ('сила', 'сильный', 'root'),
    ('белый', 'белизна', 'root'),
    ('читать', 'чтение', 'root'),
    ('дом', 'домик', 'root'),
    ('кот', 'котик', 'root'),
    ('рука', 'ручка', 'root'),
    ('нога', 'ножка', 'root'),
    ('книга', 'книжка', 'root'),
    ('мать', 'матушка', 'root'),
    ('человек', 'люди', 'lexeme'),
    ('ребёнок', 'дети', 'lexeme'),
    ('год', 'лет', 'lexeme'),
    ('хороший', 'лучше', 'lexeme'),
    ('хороший', 'лучший', 'lexeme'),
    ('плохой', 'хуже', 'lexeme'),
    ('большой', 'больший', 'grammar'),
    ('большой', 'больше', 'lexeme'),
    ('я', 'меня', 'lexeme'),
    ('мы', 'нас', 'lexeme'),
    ('один', 'первый', 'grammar'),
    ('два', 'второй', 'grammar'),
    ('три', 'третий', 'root'),
    ('пять', 'пятый', 'root'),
    # «пара» is also the genitive of «пар» (steam). That reading is enough.
    ('пара', 'испарять', 'root'),
    # «строить» also parses as a derivative of «три». That reading is enough.
    ('строить', 'три', 'root'),
    # Short masculine of «сырой» is also «сыр». Damp stays linked.
    ('сырой', 'сырость', 'root'),
    ('пологий', 'пологость', 'root'),
    # «устав» is also the gerund of «устать», so the surface reaches the fatigue family.
    ('устав', 'устать', 'lexeme'),
    ('устав', 'уставать', 'root'),
    ('устать', 'усталость', 'root'),
    ('стоять', 'стать', 'root'),
    ('стоять', 'ставить', 'root'),
    ('образ', 'образование', 'root'),
    ('разить', 'поражать', 'root'),
    ('отражать', 'поражать', 'root'),
    ('отражатель', 'отражение', 'root'),
    ('запас', 'запасать', 'root'),
    ('спасти', 'спасение', 'root'),
    ('плести', 'плетение', 'root'),
    ('грести', 'гребля', 'root'),
    ('марь', 'маревый', 'root'),
    ('мир', 'мировой', 'root'),
    ('море', 'морской', 'root'),
    ('умирать', 'смерть', 'root'),
    ('направление', 'направить', 'root'),
    ('правительство', 'правитель', 'root'),
    ('справка', 'справочник', 'root'),
    ('правда', 'правдивый', 'root'),
    ('править', 'правительство', 'root'),
    ('править', 'исправить', 'root'),
    ('заправка', 'заправить', 'root'),
    ('приправа', 'заправить', 'root'),
    # «погреб» is also a form of the verb «погрести».
    ('погреб', 'погребение', 'root'),
    ('орёл', 'Орёл', 'exact'),
    ('мёд', 'мед', 'exact'),
    ('университет', 'универ', 'alias'),
    ('фотография', 'фото', 'alias'),
    ('килограмм', 'кг', 'alias'),
)

FALSE_CASES = (
    'пчела', 'пчеловод',
    'пчела', 'пчеловодство',
    'мёд', 'медоносный',
    'мед', 'медоносный',
    'вода', 'водопад',
    'лес', 'лесостепь',
    'земля', 'землетрясение',
    'вода', 'водить',
    'лето', 'лететь',
    'семь', 'семья',
    'полный', 'половина',
    'соты', 'сотня',
    'страна', 'странный',
    'крупа', 'крупный',
    'чета', 'чётный',
    'душа', 'душный',
    'год', 'годный',
    'свет', 'светский',
    'мать', 'матка',
    'гора', 'горний',
    'голова', 'глава',
    'голова', 'уголовный',
    'голова', 'главный',
    'белый', 'белок',
    'белый', 'белка',
    'север', 'северо-западный',
    'идти', 'ходить',
    'пчила', 'пчела',
    'bee', 'пчела',
    'Moscow', 'Москва',
    'краса', 'красный',
    'плоть', 'плотный',
    'друг', 'другой',
    'лебеда', 'лебединый',
    'червь', 'червовый',
    'колос', 'колосник',
    'сырник', 'сырость',
    'ворон', 'воронение',
    'слава', 'слово',
    'слово', 'условие',
    'мел', 'мельник',
    'мел', 'молоть',
    'мелкий', 'молоть',
    'печка', 'печаль',
    'печка', 'обеспечить',
    'печка', 'пещера',
    'печка', 'печень',
    'раз', 'образ',
    'сразу', 'образ',
    'зараза', 'раз',
    'личинка', 'отличие',
    'брачный', 'забраться',
    'необходимый', 'выходить',
    'уставный', 'уставать',
    'устать', 'стоять',
    'достаточный', 'остаться',
    'цветок', 'бесцветный',
    'самец', 'самый',
    'жаль', 'жальник',
    'вкус', 'искусство',
    'вкус', 'искус',
    'стачка', 'такой',
    'мещанин', 'место',
    'простой', 'простить',
    'простой', 'простыня',
    'положить', 'пологость',
    'обруч', 'обручение',
    'стоять', 'уставный',
    'стоять', 'достаточный',
    'образ', 'выражать',
    'разить', 'выражать',
    'отражатель', 'поражать',
    'запас', 'опасность',
    'запас', 'пастух',
    'пастух', 'спасти',
    'плести', 'плот',
    'плот', 'плотник',
    'грести', 'гроб',
    'гроб', 'погреб',
    'погребок', 'погребение',
    'маревый', 'мара',
    'мир', 'море',
    'мир', 'умирать',
    'справка', 'направление',
    'справка', 'правительство',
    'правительство', 'исправить',
    'заправка', 'приправа',
    'расправа', 'расправить',
)


class SemanticsTests(SimpleTestCase):
    def test_required_matches(self):
        for guess, target, reason in TRUE_CASES:
            self.assertTrue(opens(guess, target), f'{guess}/{target} stayed closed')
            self.assertEqual(explain(guess, target), reason, f'{guess}/{target}')
            self.assertTrue(opens(target, guess), f'{target}/{guess}')

    def test_required_closures(self):
        pairs = list(zip(FALSE_CASES[0::2], FALSE_CASES[1::2]))
        for guess, target in pairs:
            self.assertFalse(
                opens(guess, target),
                f'{guess}/{target} opened as {explain(guess, target)}',
            )
            self.assertFalse(opens(target, guess), f'{target}/{guess}')

    def test_service_words_are_not_root_guesses(self):
        self.assertTrue(initially_open('бы'))
        self.assertTrue(initially_open('в'))
        self.assertTrue(initially_open('и'))
        self.assertTrue(initially_open('не'))
        self.assertFalse(opens('бы', 'быть'))
        self.assertFalse(opens('быть', 'бы'))
        self.assertFalse(initially_open('он'))
        self.assertFalse(initially_open('пчела'))
        self.assertFalse(initially_open('равно'))

    def test_generic_is_exact_only(self):
        self.assertEqual(explain('Apis', 'apis'), 'exact')
        self.assertEqual(explain('DNA', 'dna'), 'exact')
        self.assertFalse(opens('Apis', 'пчела'))
        self.assertFalse(opens('hello-world', 'hello'))
        self.assertEqual(explain('α', 'Α'), 'exact')

    def test_compound_order_does_not_matter_but_a_repeat_does(self):
        self.assertTrue(opens('белоснежный', 'снежно-белый'))
        self.assertFalse(opens('один', 'один-одинехонек'))

    def test_relations_are_not_transitive(self):
        # Alias does not pull in the other word's root family.
        self.assertEqual(explain('фото', 'фотография'), 'alias')
        self.assertFalse(opens('фото', 'фотограф'))
        self.assertFalse(opens('универ', 'университетский'))
        # Grammar does not pull in a later root neighbour.
        self.assertEqual(explain('один', 'первый'), 'grammar')
        self.assertFalse(opens('один', 'первенец'))
        # Irregular grammar does not continue into an unrelated root.
        self.assertEqual(explain('хороший', 'лучше'), 'lexeme')
        self.assertFalse(opens('кг', 'километровый'))

    def test_split_subfamilies_are_disjoint(self):
        from games.censorly.lexical.semantic_splits import CLUSTERS
        by_name = {}
        seen = {}
        for name, lemmas in CLUSTERS:
            self.assertNotIn(name, by_name)
            by_name[name] = lemmas
            for lemma in lemmas:
                self.assertNotIn(lemma, seen, f'{lemma} in {seen.get(lemma)} and {name}')
                seen[lemma] = name
        closed = (
            ('sense:beauty', 'sense:red'),
            ('sense:glory', 'sense:word'),
            ('sense:word', 'sense:condition'),
            ('sense:bloom', 'sense:color'),
            ('sense:chalk', 'sense:mill'),
            ('sense:taste', 'sense:art'),
            ('sense:care', 'sense:sadness'),
        )
        for left_name, right_name in closed:
            left = by_name[left_name][0]
            right = by_name[right_name][0]
            self.assertFalse(opens(left, right), f'{left}/{right}')
        self.assertTrue(opens('красивый', 'красота'))
        self.assertTrue(opens('красный', 'краска'))
        self.assertTrue(opens('мельник', 'молоть'))
        self.assertTrue(opens('цветок', 'цвести'))
        self.assertTrue(opens('бесцветный', 'цветной'))
        self.assertTrue(opens('простой', 'упростить'))
        self.assertTrue(opens('простить', 'прощение'))

    def test_alternative_root_readings_are_not_a_compound(self):
        from games.censorly.lexical.rootbank import structures_of

        mary = structures_of('марь')
        self.assertEqual(set(mary), {('sense:haze',), ('sense:plant',)})
        self.assertNotIn(('sense:haze', 'sense:plant'), mary)
        self.assertEqual(structures_of('маревый'), (('sense:haze',),))
        self.assertNotIn(('sense:plant',), structures_of('маревый'))
        self.assertTrue(opens('марь', 'маревый'))
        self.assertFalse(opens('маревый', 'мара'))
        compound = structures_of('пчеловод')
        self.assertTrue(compound)
        self.assertTrue(all(len(item) >= 2 for item in compound))

    def test_split_families_leave_no_bridge_lemma(self):
        from games.censorly.lexical.core import fold
        from games.censorly.lexical.rootbank import _KUZ_GROUPS, _KUZ_LEMMAS, _SENSE
        from games.censorly.lexical.russian.compiler import (
            _families, _lines, _norm, _strip_note,
        )

        families = _families(_KUZ_GROUPS)
        wanted = {
            'раж¹|раз¹', 'пас²', 'плач³|плес¹|плет|плот¹|плоч',
            'граб¹|греб|грес|гроб', 'мар³', 'прав',
        }
        missing = []
        for line in _lines(_KUZ_LEMMAS):
            raw_lemma, raw_root = line.split('\t', 1)
            lemma = fold(_strip_note(raw_lemma))
            root = _norm(raw_root.strip())
            family = families.get(root, root)
            if family in wanted and lemma not in _SENSE:
                missing.append(f'{family}:{lemma}')
        self.assertEqual(missing, [])


class LexicalGameplayTests(TestCase):
    """Flag on changes masks through apply_guess. Flag off stays on lemma match."""

    BODY = (
        'Пчёлами полон улей. Пчелиный мёд сладок. Пчеловод работает. '
        'Медовый вкус рядом. Медоносный цветок. Белки лежат. '
        'Выходить рано. Три окна. Испарять воду. Странный случай. '
        'Матка пчелы. Университет открыт. Фотография ясна. Просто факт.'
    )

    def _task(self):
        from games.tests.test_censorly import _make_puzzle_task
        return _make_puzzle_task(title='Корзина', body=self.BODY)

    def _revealed(self, game, task, user):
        import json
        from games.models import ChainTaskState
        row = ChainTaskState.objects.get(
            task=task, game=game, game_mode='general',
            user=user, team=None, anon_key=None,
        )
        return set(json.loads(row.state).get('revealed_lemmas') or [])

    def _surfaces_for_ids(self, puzzle, ids):
        by_id = {
            tok['id']: tok
            for tok in (puzzle.get('title_tokens') or []) + (puzzle.get('body_tokens') or [])
        }
        return [fold(by_id[item].get('surface') or '') for item in ids]

    def _open_texts(self, result):
        from games.censorly.lexical.core import fold
        return {
            fold(tok.get('text') or '')
            for tok in result['body_tokens']
            if tok.get('revealed') and tok.get('text')
        }

    def test_flag_off_does_not_open_root_neighbours(self):
        from django.test import override_settings
        from games.censorly.play import apply_guess
        game, task, _hash, puzzle = self._task()
        user = User.objects.create_user('cz_lex_off', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=False):
            result = apply_guess(game=game, task=task, word='пчела', user=user)
        revealed = self._revealed(game, task, user)
        texts = self._open_texts(result)
        self.assertEqual(
            self._surfaces_for_ids(puzzle, result['guesses'][-1]['token_ids']),
            [fold('Пчёлами'), fold('пчелы')],
        )
        self.assertIn(fold('пчёлами'), texts)
        self.assertNotIn(fold('пчелиный'), texts)
        self.assertNotIn(fold('пчеловод'), texts)
        self.assertTrue(revealed)
        self.assertNotIn(fold('пчелиный'), {fold(item) for item in revealed})
        from games.models import StatisticsEvent
        self.assertFalse(StatisticsEvent.objects.filter(
            kind=StatisticsEvent.KIND_CENSORLY_LEXICAL_GUESS,
        ).exists())

    def test_flag_on_updates_state_and_masks(self):
        from django.test import override_settings
        from games.censorly.normalize import lemma_of
        from games.censorly.play import apply_guess, get_play_state
        game, task, _hash, puzzle = self._task()
        user = User.objects.create_user('cz_lex_on', password='x')
        with override_settings(CENSORLY_LEXICAL_RESOLVER=True):
            before = get_play_state(game=game, task=task, user=user)
            opened = self._open_texts(before)
            self.assertIn(fold('просто'), opened)
            bee = apply_guess(game=game, task=task, word='пчела', user=user)
            self.assertEqual(
                self._surfaces_for_ids(puzzle, bee['guesses'][-1]['token_ids']),
                [fold('Пчёлами'), fold('Пчелиный'), fold('пчелы')],
            )
            texts = self._open_texts(bee)
            self.assertIn(fold('пчёлами'), texts)
            self.assertIn(fold('пчелиный'), texts)
            self.assertNotIn(fold('пчеловод'), texts)
            from games.models import StatisticsEvent
            logged = StatisticsEvent.objects.get(
                kind=StatisticsEvent.KIND_CENSORLY_LEXICAL_GUESS,
                payload__guess='пчела',
            )
            opened = {fold(item['lemma']) for item in logged.payload['opened']}
            rejected = {
                (fold(item['lemma']), item['reason'])
                for item in logged.payload['rejected']
            }
            self.assertIn(fold('пчелиный'), opened)
            self.assertIn(fold('пчела'), opened)
            self.assertIn((fold('пчеловод'), 'root_overlap'), rejected)
            self.assertNotIn(fold('пчеловод'), opened)
            honey = apply_guess(game=game, task=task, word='мёд', user=user)
            honey_texts = self._open_texts(honey)
            self.assertIn(fold('медовый'), honey_texts)
            self.assertNotIn(fold('медоносный'), honey_texts)
            protein = apply_guess(game=game, task=task, word='белок', user=user)
            self.assertIn(fold('белки'), self._open_texts(protein))
            squirrel = apply_guess(game=game, task=task, word='белка', user=user)
            self.assertIn(fold('белки'), self._open_texts(squirrel))
            walk = apply_guess(game=game, task=task, word='ходить', user=user)
            self.assertIn(fold('выходить'), self._open_texts(walk))
            triple = apply_guess(game=game, task=task, word='строить', user=user)
            self.assertIn(fold('три'), self._open_texts(triple))
            steam = apply_guess(game=game, task=task, word='пара', user=user)
            self.assertIn(fold('испарять'), self._open_texts(steam))
            country = apply_guess(game=game, task=task, word='страна', user=user)
            self.assertNotIn(fold('странный'), self._open_texts(country))
            mother = apply_guess(game=game, task=task, word='мать', user=user)
            self.assertNotIn(fold('матка'), self._open_texts(mother))
            alias = apply_guess(game=game, task=task, word='универ', user=user)
            self.assertIn(fold('университет'), self._open_texts(alias))
            photo = apply_guess(game=game, task=task, word='фото', user=user)
            self.assertIn(fold('фотография'), self._open_texts(photo))
        revealed = self._revealed(game, task, user)
        self.assertIn(lemma_of('пчелиный'), revealed)
        self.assertNotIn(lemma_of('пчеловод'), revealed)
