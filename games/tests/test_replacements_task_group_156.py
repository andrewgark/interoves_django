# -*- coding: utf-8 -*-
"""
Регрессия разбора «Весь текст» для набора «Замены (с ошибкой)» (игра replacements, №156).

https://interoves.com/games/replacements/156/
"""
from django.test import SimpleTestCase

from games.replacements_input_parse import (
    literals_from_right_tokens,
    parse_repl_line_answers_smart,
)
from games.replacements_lines import parse_replacements_lines_text

# Текст задания (левая колонка) с сайта.
TASK_156_LEFT = """РАБОТЯЩЕГО ГУСИКА запускали на РАБОТЕ в ЛИБЕРТИ-СИТИ
На РАБОТЕ в ЛИБЕРТИ-СИТИ впервые был РАБОТЯЩИЙ ПРЕСТУПНИК
В ЛИБЕРТИ-СИТИ также проводится ежегодный турнир BUSINESSman
На РАБОТЕ можно получить ПАРЛАМЕНТСКУЮ ПРЕМИЮ.
На РАБОТЕ можно получить КОЛОНИАЛЬНУЮ ПРЕМИЮ
Одно из значений слов от которых произошли названия «БИЗНЕС» и «БИЗНЕС» – имена ПОВТОРНЫХ ДЕЛЬЦОВ
ПОВТОР проиграл СЛОВА КАЛЬКЕ.
КАЛЬКА-ЕРРОР находятся в США, ОАЭ, Уругвае, Азербайджане и Турции
«В УПРЯЖКЕ ПОВТОРНОГО ОЛЕНЯ» играет в эпизоде "Frank Retires" сериала "It's Always Sunny in Philadelphia".
ОЛЕНЬ СВЕН пил чай с ГУСЁНКОМ НЕНАЗВАННЫМ на свой КОЛОНИАЛЬНЫЙ СТИЛЬ.
ОЛЕНЬ СВЕН был на открытии РАБОТЫ в РОВАНИЕМИ вместе с САНТА КЛАУСОМ
НЕНАЗВАННЫЙ и улица КЛАУСА – соседние станции на SVEN line
ХЕЛЬГЕ СВЕНСЕН в 2018-ом году стал первым, кто не проиграл 100 партий подряд.
В фильме «ПРАЗДНИК СЛОВ» упоминается СОЮЗ ПАРЛАМЕНТА и КОЛОНИИ
В ПРЕСТУПНОЙ СВОБОДЕ можно нанять АДВОКАТОВ (ДЕЛЬЦОВ ПРЕСТУПНИКА).
Римское имя для ПРАЗДНИЧНОЙ звезды стало именем АДВОКАТА."""


def _parsed():
    return parse_replacements_lines_text(TASK_156_LEFT, '')


def _line_literals_and_slots(line_idx):
    p = _parsed()
    lt = p['right_tokens'][line_idx]
    n = sum(1 for t in lt if t['type'] == 'slot')
    return literals_from_right_tokens(lt), n


class ReplacementsTaskGroup156WholeTextTests(SimpleTestCase):
    """Строки 3, 6, 15 — типичные сбои при вставке целых фраз в «Весь текст»."""

    def test_line3_user_decoded_phrase_with_cobaltman(self):
        """Расшифровка: другое слово вместо BUSINESSman в хвосте — не слот."""
        L, n = _line_literals_and_slots(2)
        phrase = 'В ЛЭЙК-ПЛЭСИД также проводится ежегодный турнир COBALTman'
        self.assertEqual(
            parse_repl_line_answers_smart(phrase, n, L),
            ['ЛЭЙК', 'ПЛЭСИД'],
        )

    def test_line3_hyphen_or_space_in_compound(self):
        L, n = _line_literals_and_slots(2)
        self.assertEqual(n, 2)
        full = (
            'В STATEN-ISLAND также проводится ежегодный турнир BUSINESSman'
        )
        self.assertEqual(
            parse_repl_line_answers_smart(full, n, L),
            ['STATEN', 'ISLAND'],
        )
        with_space = (
            'В STATEN ISLAND также проводится ежегодный турнир BUSINESSman'
        )
        self.assertEqual(
            parse_repl_line_answers_smart(with_space, n, L),
            ['STATEN', 'ISLAND'],
        )

    def test_line3_answer_only_tokens_or_full_phrase(self):
        L, n = _line_literals_and_slots(2)
        self.assertEqual(parse_repl_line_answers_smart('LIBERTY\tCITY', n, L), ['LIBERTY', 'CITY'])
        self.assertEqual(
            parse_repl_line_answers_smart('LIBERTY CITY', n, L),
            ['LIBERTY', 'CITY'],
        )
        self.assertEqual(
            parse_repl_line_answers_smart(
                'В LIBERTY CITY также проводится ежегодный турнир BUSINESSman', n, L
            ),
            ['LIBERTY', 'CITY'],
        )

    def test_line6_ascii_dash_before_imena(self):
        L, n = _line_literals_and_slots(5)
        self.assertEqual(n, 4)
        ascii_dash = (
            'Одно из значений слов от которых произошли названия «a» и «b»'
            ' - имена c d'
        )
        en_dash = (
            'Одно из значений слов от которых произошли названия «a» и «b»'
            ' – имена c d'
        )
        self.assertEqual(
            parse_repl_line_answers_smart(ascii_dash, n, L),
            ['a', 'b', 'c', 'd'],
        )
        self.assertEqual(
            parse_repl_line_answers_smart(en_dash, n, L),
            ['a', 'b', 'c', 'd'],
        )

    def test_line6_four_words_only(self):
        L, n = _line_literals_and_slots(5)
        self.assertEqual(
            parse_repl_line_answers_smart('w1 w2 w3 w4', n, L),
            ['w1', 'w2', 'w3', 'w4'],
        )

    def test_line4_optional_trailing_period(self):
        L, n = _line_literals_and_slots(3)
        self.assertEqual(n, 3)
        self.assertEqual(
            parse_repl_line_answers_smart('На JOB можно получить ADJ NOUN.', n, L),
            ['JOB', 'ADJ', 'NOUN'],
        )
        self.assertEqual(
            parse_repl_line_answers_smart('На JOB можно получить ADJ NOUN', n, L),
            ['JOB', 'ADJ', 'NOUN'],
        )

    def test_line7_optional_trailing_period(self):
        L, n = _line_literals_and_slots(6)
        self.assertEqual(n, 3)
        self.assertEqual(
            parse_repl_line_answers_smart('REPEAT проиграл WORDS CHALK.', n, L),
            ['REPEAT', 'WORDS', 'CHALK'],
        )
        self.assertEqual(
            parse_repl_line_answers_smart('REPEAT проиграл WORDS CHALK', n, L),
            ['REPEAT', 'WORDS', 'CHALK'],
        )

    def test_line15_optional_closing_punct(self):
        L, n = _line_literals_and_slots(14)
        self.assertEqual(n, 5)
        phrase = 'В ADJ NOUN можно нанять LAWYERS (DEALERS CRIMINAL).'
        self.assertEqual(
            parse_repl_line_answers_smart(phrase, n, L),
            ['ADJ', 'NOUN', 'LAWYERS', 'DEALERS', 'CRIMINAL'],
        )
        self.assertEqual(
            parse_repl_line_answers_smart(phrase.rstrip('.'), n, L),
            ['ADJ', 'NOUN', 'LAWYERS', 'DEALERS', 'CRIMINAL'],
        )

    def test_whole_text_block_all_sixteen_lines_parse(self):
        """Синтетические ответы в шаблоне каждой строки — все 16 строк разбираются."""
        p = _parsed()
        bad = []
        for i, lt in enumerate(p['right_tokens']):
            n = sum(1 for t in lt if t['type'] == 'slot')
            L = literals_from_right_tokens(lt)
            parts = []
            si = 0
            for tok in lt:
                if tok['type'] == 'text':
                    parts.append(tok['text'])
                else:
                    parts.append('A%s' % si)
                    si += 1
            raw = ''.join(parts)
            got = parse_repl_line_answers_smart(raw, n, L)
            if got is None or len(got) != n:
                bad.append(i + 1)
        self.assertEqual(bad, [], 'failed lines: %s' % bad)
