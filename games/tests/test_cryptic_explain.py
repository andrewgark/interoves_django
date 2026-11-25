"""Разбор криптика: язык разметки и формул, консоль, показ после решения, правила."""

from __future__ import annotations

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

    def test_markup_is_escaped(self):
        html = explanation_html(
            clue='<b>x</b> мужчина же сокровище', answer='ЖЕМЧУЖИНА',
            markup='*<b>x</b>* (мужчина же) | {сокровище}', formula='анаг[<b>x</b>](МУЖЧИНА ЖЕ)',
        )
        self.assertNotIn('<b>', html)


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
