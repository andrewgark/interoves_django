"""Разбор криптика: язык разметки и формул, консоль, показ после решения, правила."""

from __future__ import annotations

import time

from django.test import Client, SimpleTestCase, TestCase

from games.console.games.simple_daily import SimpleDailyError
from games.console.games.simple_daily_sections import console_for
from games.console.http.constants import CRYPTIC_CONSOLE_GROUP
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID
from games.models import Task
from games.puzzles.cryptic.explain import analyze, explanation_html
from games.tests.test_advent import _cryptic_game, _post, _user

GOOD = [
    # (clue, answer, markup, formula)
    ('Пьяный мужчина же сокровище', 'ЖЕМЧУЖИНА',
     '*Пьяный* (мужчина же) | {сокровище}', 'анаг[Пьяный](МУЖЧИНА ЖЕ)'),
    ('Мир средь бела дня', 'ЛАД',
     '{Мир} | *средь* (бела дня)', 'скрыт[средь](БЕЛА ДНЯ)=ЛАД'),
    ('Он сначала бьёт, значит это родные края', 'ОТЧИЗНА',
     '(Он) *сначала* *бьёт*, (значит) | {это родные края}', 'нач[сначала](ОН) + анаг[бьёт](ЗНАЧИТ)'),
    ('Бессердечный ходок взял мяч, возможно, Навального', 'ХОМЯЧОК',
     '*Бессердечный* (ходок) *взял* (мяч), | {возможно, Навального}',
     'вокруг[взял](без_сер[Бессердечный](ХОДОК), МЯЧ)'),
    ('Бесконечно говорю, что пора падать по спирали', 'ШТОПОР',
     '*Бесконечно* *говорю*, (что) (пора) | {падать по спирали}',
     'звук[говорю](ЧТО)=ШТО + без_кон[Бесконечно](ПОРА)'),
    ('Лидеры и чиновники европейских государств окружены, но нормально, сойдёт', 'НИЧЕГО',
     '*Лидеры* (и чиновники европейских государств) *окружены*, (но) | {нормально, сойдёт}',
     'вокруг[окружены](НО, нач[Лидеры](И ЧИНОВНИКИ ЕВРОПЕЙСКИХ ГОСУДАРСТВ)=ИЧЕГ)'),
    ('Моя фигура 0 лет назад', 'ТЕЛО', '{Моя фигура} | (0 лет) *назад*', 'обр[назад](0=О + ЛЕТ)'),
    ('«Начала» Евклида в различных областях в Греции используют как единицу', 'ЕВРО',
     '*«Начала»* (Евклида в различных областях) | {в Греции используют как единицу}',
     'нач[«Начала»](ЕВКЛИДА В РАЗЛИЧНЫХ ОБЛАСТЯХ)'),
    ('Храню вечно чьи-то поздние часы', 'НОЧЬ',
     '*Храню* (вечно чьи-то) | {поздние часы}', 'скрыт[Храню](ВЕЧНО ЧЬИ-ТО)=НОЧЬ'),
    ('Лучше, чтобы не отказывал вам политический институт', 'ОРГАН',
     '{Лучше, чтобы не отказывал вам} | {политический институт}', 'ОРГАН'),
    ('Стойка через раз — сок', 'СОК', '(Стойка) *через раз* — | {сок}', 'нечёт[через раз](СТОЙКА)'),
    ('Кот, сменив к на р, — рот', 'РОТ', '(Кот), *сменив* к на р, — | {рот}', 'замена[сменив](КОТ, К→Р)'),
    ('Слон без с — лон', 'ЛОН', '(Слон) *без* (с) — | {лон}', 'убрать[без](СЛОН, С)'),
    ('Слон, с — лон', 'ЛОН', '(Слон), (с) — | {лон}', 'СЛОН − С'),
    ('Река, затем рог — порог', 'ПОРОГ', '(Река), *затем* (рог) — | {порог}', 'склеить[затем](РЕКА=ПО, РОГ)'),
]

CLUE = 'Пьяный мужчина же сокровище'
MARKUP = '*Пьяный* (мужчина же) | {сокровище}'


def _errors(formula, markup=MARKUP, clue=CLUE, answer='ЖЕМЧУЖИНА'):
    return analyze(clue=clue, answer=answer, markup=markup, formula=formula).errors


class LanguageTests(SimpleTestCase):
    def test_worked_examples_check_out(self):
        for clue, answer, markup, formula in GOOD:
            with self.subTest(answer=answer):
                res = analyze(clue=clue, answer=answer, markup=markup, formula=formula)
                self.assertEqual(res.errors, [])

    def test_steps_follow_the_tree(self):
        res = analyze(clue=GOOD[3][0], answer='ХОМЯЧОК', markup=GOOD[3][2], formula=GOOD[3][3])
        self.assertEqual(
            [(s.inputs, s.name, s.output) for s in res.steps],
            [(('ХОДОК',), 'без середины', 'ХООК'), (('МЯЧ', 'ХООК'), 'вставить внутрь', 'ХОМЯЧОК')],
        )
        res = analyze(clue=GOOD[2][0], answer='ОТЧИЗНА', markup=GOOD[2][2], formula=GOOD[2][3])
        self.assertEqual([s.output for s in res.steps], ['О', 'ТЧИЗНА', 'ОТЧИЗНА'])

    def test_each_indicator_colours_its_own_block(self):
        res = analyze(clue=GOOD[4][0], answer='ШТОПОР', markup=GOOD[4][2], formula=GOOD[4][3])
        inds = {n.text: n.color for n in res.surface.walk() if n.kind == 'ind'}
        # «говорю» — первая операция в формуле, «Бесконечно» — вторая; блоки идут крест-накрест.
        self.assertEqual(inds, {'говорю': 1, 'Бесконечно': 2})
        self.assertEqual(res.fod_colors, {0: 1, 1: 2})

    def test_mistakes_are_named(self):
        self.assertIn('Слово «ЖЕ» из блока (…) не использовано в формуле', _errors('анаг[Пьяный](МУЖЧИНА)'))
        self.assertIn('В формуле «КОТ», а в блоках (…) такого слова нет', _errors('анаг[Пьяный](МУЖЧИНА ЖЕ КОТ)'))
        self.assertEqual(
            _errors('анаг(МУЖЧИНА ЖЕ)'),
            ['Индикатор «Пьяный» не привязан к операции: напишите op[Пьяный](…)'],
        )
        self.assertEqual(_errors('анаг[Трезвый](МУЖЧИНА ЖЕ)')[0], 'Индикатора «Трезвый» нет в разметке *…*')
        self.assertEqual(_errors('обр[Пьяный](МУЖЧИНА ЖЕ)'), ['Формула даёт ЕЖАНИЧЖУМ, а не ЖЕМЧУЖИНА'])
        self.assertEqual(_errors('скрыт[Пьяный](МУЖЧИНА ЖЕ)=ЖЕМ'), ['Шаг «скрыт(…)» не даёт ЖЕМ'])
        self.assertEqual(_errors('звук[Пьяный](МУЖЧИНА ЖЕ)'), ['У «звук» допишите результат: звук(…)=СЛОВО'])
        self.assertIn('ожидалось «)»', _errors('анаг[Пьяный](МУЖЧИНА ЖЕ')[0])

    def test_markup_rules(self):
        self.assertEqual(_errors('анаг[Пьяный](МУЖЧИНА ЖЕ)', markup='*Пьяный* (мужчина же) {сокровище}'),
                         ['Нет черты |, делящей подсказку на две части'])
        self.assertEqual(_errors('анаг[Пьяный](МУЖЧИНА ЖЕ)', markup='*Пьяный* (мужчина же) | сокровище'),
                         ['Нет смысловой части {…}'])
        self.assertIn('Текст без служебных знаков не совпадает с подсказкой',
                      _errors('анаг[Пьяный](МУЖЧИНА ЖЕ)', markup='*Пьяный* (мужчина же) | {клад}'))
        self.assertEqual(
            _errors('ОРГАН', clue='Орган', answer='ОРГАН', markup='{Орган} |'),
            ['Формула — сам ответ только у двойного определения: нужны две {…}'],
        )

    def test_player_sees_only_a_checked_explanation(self):
        html = explanation_html(clue=CLUE, answer='ЖЕМЧУЖИНА', markup=MARKUP, formula='анаг[Пьяный](МУЖЧИНА ЖЕ)')
        self.assertIn('cx-seg--def', html)
        self.assertIn('>анаграмма<', html)
        self.assertIn('ЖЕМЧУЖИНА', html)
        self.assertEqual(explanation_html(clue=CLUE, answer='ЖЕМЧУЖИНА', markup=MARKUP, formula='обр(X)'), '')
        hidden = explanation_html(clue='Мир средь бела дня', answer='ЛАД',
                                  markup=GOOD[1][2], formula=GOOD[1][3])
        self.assertIn('БЕ<span class="cx-hit">ЛА Д</span>НЯ', hidden)

    def test_picked_letters_are_marked(self):
        def marked(markup, answer, formula):
            clue = ' '.join(markup.translate(str.maketrans('', '', '*(){}|')).split())
            html = explanation_html(clue=clue, answer=answer, markup=markup, formula=formula)
            self.assertTrue(html, formula)
            return html

        # первые буквы — каждая отдельно, даже у соседних слов
        self.assertIn('<span class="cx-hit">М</span>ИРА <span class="cx-hit">И</span> <span class="cx-hit">Р</span>АЯ',
                      marked('*Начала* (мира и рая) | {мир}', 'МИР', 'нач[Начала](МИРА И РАЯ)'))
        self.assertIn('СА<span class="cx-hit">М</span> Л<span class="cx-hit">И</span> ТИ<span class="cx-hit">Р</span>',
                      marked('*Концы* (сам ли тир) | {мир}', 'МИР', 'кон[Концы](САМ ЛИ ТИР)'))
        self.assertIn('С<span class="cx-hit">УМ</span>А', marked('*Сердце* (сума) | {ум}', 'УМ', 'сер[Сердце](СУМА)'))
        self.assertIn('А<span class="cx-hit">М</span>И<span class="cx-hit">И</span>Р<span class="cx-hit">Б</span>',
                      marked('*Чётные* (амиирб) | {миб}', 'МИБ', 'чёт[Чётные](АМИИРБ)'))
        # «без …» зачёркивает выброшенное, вставка выделяет вставленное в результате
        homyak = marked(GOOD[3][2], GOOD[3][1], GOOD[3][3])
        self.assertIn('ХО<span class="cx-cut">Д</span>ОК', homyak)
        self.assertIn('ХО<span class="cx-hit">МЯЧ</span>ОК', homyak)
        self.assertIn('<span class="cx-cut">Д</span>УМА',
                      marked('*Безначальная* (дума) | {ума}', 'УМА', 'без_нач[Безначальная](ДУМА)'))
        self.assertIn('<span class="cx-cut">ОК</span>НО', marked('(Окно) (ок) | {но}', 'НО', 'ОКНО − ОК'))
        swap = marked('*Поменяв* (кот) | {рот}', 'РОТ', 'замена[Поменяв](КОТ, К→Р)')
        self.assertIn('<span class="cx-cut">К</span>ОТ', swap)
        self.assertIn('<span class="cx-hit">Р</span>ОТ', swap)
        # первые буквы склейки: слова берутся из обеих частей
        self.assertIn('<span class="cx-hit">М</span>ИРА + <span class="cx-hit">И</span> <span class="cx-hit">Р</span>АЯ',
                      marked('*Начала* (мира) (и рая) | {мир}', 'МИР', 'нач[Начала](МИРА + И РАЯ)'))

    def test_edge_letters_and_middle(self):
        res = analyze(clue='Хвосты сам тир амир', answer='АМИР', markup='*Хвосты* (сам тир) | {амир}',
                      formula='кон2[Хвосты](САМ ТИР)')
        self.assertEqual(res.errors, [])
        self.assertEqual((res.steps[0].name, res.steps[0].hit), ('последние 2 буквы', (1, 2, 4, 5)))
        self.assertEqual(_errors('сер[Сердце](СУМА)', clue='Сердце сума ум', answer='УМ',
                                 markup='*Сердце* (сума) | {ум}'), [])
        # у чётной длины подходит и одна средняя буква
        res = analyze(clue='Сердце сума у', answer='У', markup='*Сердце* (сума) | {у}', formula='сер[Сердце](СУМА)')
        self.assertEqual((res.errors, res.steps[0].hit), ([], (1,)))
        res = analyze(clue='Бессердечная сума сма', answer='СМА', markup='*Бессердечная* (сума) | {сма}',
                      formula='без_сер[Бессердечная](СУМА)')
        self.assertEqual((res.errors, res.steps[0].cut), ([], (1,)))

    def test_hover_links_steps_to_clue(self):
        html = explanation_html(clue=GOOD[3][0], answer=GOOD[3][1], markup=GOOD[3][2], formula=GOOD[3][3])
        self.assertTrue(html.startswith('<div class="cx-explain">'))
        # без_сер (id 2): индикатор, блок и шаг; вставка (id 1) — последний шаг, связан с определением
        self.assertEqual(html.count('data-cx-ids="2"'), 3)
        self.assertIn('<li class="cx-step cx-c1" data-cx-ids="1 def">', html)
        self.assertIn('cx-seg--def" data-cx-ids="def"', html)
        bare = explanation_html(clue='Окно ок но', answer='НО', markup='(Окно) (ок) | {но}', formula='ОКНО − ОК')
        self.assertEqual(bare.count('data-cx-ids="3'), 3)  # оба блока и шаг «убрать»

    def test_markup_is_escaped(self):
        html = explanation_html(
            clue='<b>x</b> мужчина же сокровище', answer='ЖЕМЧУЖИНА',
            markup='*<b>x</b>* (мужчина же) | {сокровище}', formula='анаг[<b>x</b>](МУЖЧИНА ЖЕ)',
        )
        self.assertNotIn('<b>', html)

    def test_nested_glue_shows_its_letters(self):
        res = analyze(clue='Тело с 0 лет назад', answer='ТЕЛО', markup='{Тело} | *с* (0 лет) *назад*',
                      formula='обр[назад](склеить[с](0=О, ЛЕТ))')
        self.assertEqual([(s.inputs, s.output) for s in res.steps][1:],
                         [(('О', 'ЛЕТ'), 'ОЛЕТ'), (('ОЛЕТ',), 'ТЕЛО')])

    def test_display_keeps_yo_and_spaces(self):
        res = analyze(clue='Разбитый лёд мой — лёд', answer='дёл', markup='*Разбитый* (лёд) | {мой — лёд}',
                      formula='анаг[Разбитый](ЛЁД)')
        self.assertEqual((res.steps[0].inputs, res.steps[0].output), (('ЛЁД',), 'ДЁЛ'))
        res = analyze(clue='Странная тропа лесная — тропа', answer='лесная  тропа',
                      markup='*Странная* (тропа лесная) | {— тропа}', formula='анаг[Странная](ТРОПА ЛЕСНАЯ)')
        self.assertEqual(res.steps[-1].output, 'ЛЕСНАЯ ТРОПА')

    def test_every_minus_spelling(self):
        for formula in ('СЛОН - С', 'СЛОН – С', 'СЛОН — С', 'СЛОН − С'):
            with self.subTest(formula=formula):
                res = analyze(clue='Слон с — лон', answer='ЛОН', markup='(Слон) (с) — | {лон}', formula=formula)
                self.assertEqual(res.errors, [])

    def test_latin_lookalikes_are_named(self):
        errors = _errors('анаг[Пьяный](MУЖЧИНА ЖЕ)')
        self.assertIn('В формуле «MУЖЧИНА», а в блоках (…) такого слова нет (в нём латинские буквы)', errors)

    def test_operation_without_indicator_is_a_warning(self):
        res = analyze(clue=CLUE, answer='ЖЕМЧУЖИНА', markup='Пьяный (мужчина же) | {сокровище}',
                      formula='анаг(МУЖЧИНА ЖЕ)')
        self.assertEqual(res.errors, [])
        self.assertEqual(res.warnings, ['У «анаг» нет индикатора: обычно его подсказывает слово в подсказке'])

    def test_branching_formula_stays_cheap(self):
        a, b = 'АБВГДЕЖЗИКЛМНОПРСТУ', 'ФХЦЧШЩЭЮЯ'
        inner = 'вокруг(вокруг({}, {}), {})'.format(a, b, b)
        started = time.monotonic()
        res = analyze(clue='x', answer='Я', markup='{x} | (a)', formula='вокруг({0}, {0})'.format(inner))
        self.assertTrue(res.errors)
        self.assertLess(time.monotonic() - started, 2)


class ConsoleExplanationTests(TestCase):
    def setUp(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        self.console = console_for(CRYPTIC_GAME_ID)

    def _create(self, **fields):
        return self.console.create(at_number=1, fields={'clue': CLUE, 'answer': 'ЖЕМЧУЖИНА', **fields})

    def test_explanation_is_stored_and_must_check_out(self):
        item = self._create(markup=MARKUP, formula='анаг[Пьяный](МУЖЧИНА ЖЕ)')
        self.assertEqual(item['fields']['formula'], 'анаг[Пьяный](МУЖЧИНА ЖЕ)')
        task = Task.objects.get(task_group_id=item['task_group_id'])
        self.assertEqual(task.tags['cryptic_markup'], MARKUP)
        with self.assertRaisesMessage(SimpleDailyError, 'Формула даёт'):
            self.console.update(item['link_id'], fields={'formula': 'обр[Пьяный](МУЖЧИНА ЖЕ)'})
        with self.assertRaisesMessage(SimpleDailyError, 'Заполните формулу'):
            self.console.update(item['link_id'], fields={'formula': ''})
        # Подсказку поменяли, а разметку нет: такой разбор не сохранится.
        with self.assertRaisesMessage(SimpleDailyError, 'не совпадает с подсказкой'):
            self.console.update(item['link_id'], fields={'clue': 'Пьяный мужчина же клад'})
        self.console.update(item['link_id'], fields={'markup': '', 'formula': ''})
        task.refresh_from_db()
        self.assertEqual(task.tags['cryptic_markup'], '')

    def test_preview_endpoint_and_help(self):
        client = Client()
        client.force_login(_user('cryptic_editor', group=CRYPTIC_CONSOLE_GROUP))
        page = client.get('/support/cryptic/')
        self.assertContains(page, 'Как записывать разбор')
        self.assertContains(page, 'data-help-open')
        ok = _post(client, 'support:cryptic_preview', {'fields': {
            'clue': CLUE, 'answer': 'жемчужина', 'markup': MARKUP, 'formula': 'анаг[Пьяный](МУЖЧИНА ЖЕ)',
        }}).json()
        self.assertEqual((ok['errors'], ok['answer']), ([], 'ЖЕМЧУЖИНА'))
        self.assertIn('cx-steps', ok['html'])
        bad = _post(client, 'support:cryptic_preview', {'fields': {
            'clue': CLUE, 'answer': 'жемчужина', 'markup': MARKUP, 'formula': 'анаг[Пьяный](МУЖЧИНА)',
        }}).json()
        self.assertTrue(bad['errors'])
        self.assertTrue(_post(client, 'support:cryptic_preview', {'fields': {}}).json()['empty'])


class SiteExplanationTests(TestCase):
    def test_solved_card_shows_the_explanation_and_rules_explain_the_format(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        item = console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={
            'clue': CLUE, 'answer': 'ЖЕМЧУЖИНА', 'markup': MARKUP, 'formula': 'анаг[Пьяный](МУЖЧИНА ЖЕ)',
        })
        task = Task.objects.get(task_group_id=item['task_group_id'])
        client = Client()
        client.force_login(_user('cryptic_solver'))
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'как решать')
        self.assertContains(page, 'Спор внутри центра Сколково')
        self.assertContains(page, 'Какие бывают приёмы')
        self.assertNotContains(page, 'aria-label="Разбор"')
        client.post('/send_attempt/{}/'.format(task.pk), {'text': 'жемчужина', 'game_id': 'cryptic'})
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'aria-label="Разбор"')
        self.assertContains(page, 'cx-seg--ind cx-c1')

    def test_answer_button_shows_the_explanation_too(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00', show_answer_button=True)
        item = console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={
            'clue': CLUE, 'answer': 'ЖЕМЧУЖИНА', 'markup': MARKUP, 'formula': 'анаг[Пьяный](МУЖЧИНА ЖЕ)',
        })
        task = Task.objects.get(task_group_id=item['task_group_id'])
        client = Client()
        client.force_login(_user('cryptic_peeker'))
        client.post('/send_attempt/{}/'.format(task.pk), {'text': 'жемчужина', 'game_id': 'cryptic'})
        html = client.get('/answer/{}/'.format(task.pk)).json()['html']
        self.assertIn('ЖЕМЧУЖИНА', html)
        self.assertIn('cx-steps', html)
