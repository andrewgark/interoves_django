from django.test import SimpleTestCase

from games.replacements_input_parse import parse_repl_line_answers_smart_no_dom


class ReplacementsInputParseTests(SimpleTestCase):
    def test_user_snippet_four_slots_kak_dela_style(self):
        """Фрагмент пользователя: запятая, кириллическое «и», пояснение в конце."""
        snippet = (
            '"КОМАН СОВА", "ХАУ Ю ФИЛИН"  и "ХАУ ДЮ Ю ДЮ" - примерно так звучит '
            '"КАК ДЕЛА?" на разных языках.'
        )
        got = parse_repl_line_answers_smart_no_dom(snippet, 4)
        self.assertEqual(
            got,
            ['КОМАН СОВА', 'ХАУ Ю ФИЛИН', 'ХАУ ДЮ Ю ДЮ', 'КАК ДЕЛА?'],
        )

    def test_eleven_slots_doc_style_extra_trailing_quote(self):
        """11 слотов, группы через запятую и «и», в конце лишняя пара кавычек «КАК ДЕЛА?»."""
        w = ['"СЛОВО%s"' % i for i in range(11)]
        line = (
            ', '.join(w[:3])
            + '  и '
            + ', '.join(w[3:6])
            + ' - примерно так звучит '
            + ', '.join(w[6:11])
            + ' на языках. "КАК ДЕЛА?"'
        )
        got = parse_repl_line_answers_smart_no_dom(line, 11)
        self.assertEqual(len(got), 11)
        for i in range(11):
            self.assertEqual(got[i], 'СЛОВО%s' % i)

    def test_leading_blank_lines(self):
        line = ' и '.join('"W%s"' % i for i in range(11))
        raw = '\n\n  \n' + line + '\n'
        got = parse_repl_line_answers_smart_no_dom(raw, 11)
        self.assertEqual(len(got), 11)
        for i in range(11):
            self.assertEqual(got[i], 'W%s' % i)

    def test_typographic_quotes(self):
        s = '\u201cКОТ\u201d, \u201cПЕС\u201d'
        got = parse_repl_line_answers_smart_no_dom(s, 2)
        self.assertEqual(got, ['КОТ', 'ПЕС'])

    def test_four_quoted_groups_expand_to_eleven_slots_lang_line(self):
        """Как строка ответа к первой строке «языки»: 4 кавычки, внутри — 11 слов для слотов."""
        s = (
            '"КОМАН СОВА", "ХАУ Ю ФИЛИН"  и "ХАУ ДЮ Ю ДЮ" - примерно так звучит '
            '"КАК ДЕЛА?" на разных языках.'
        )
        got = parse_repl_line_answers_smart_no_dom(s, 11)
        self.assertEqual(
            got,
            ['КОМАН', 'СОВА', 'ХАУ', 'Ю', 'ФИЛИН', 'ХАУ', 'ДЮ', 'Ю', 'ДЮ', 'КАК', 'ДЕЛА'],
        )
