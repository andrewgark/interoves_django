# Парсинг заданий типа "Замены" (replacements_lines).
#
# Input: текст, где слоты — (1) слова из 2+ подряд символов Unicode Lu (ALL CAPS, в т.ч. Ë, Ä, А-Я),
#                         (2) любой фрагмент _между подчёркиваниями_, в т.ч. _23_ или _76_.
# Число без слота: просто 76 в тексте (без _…_). Слева в задании _76_ для читаемости снимаются в 76
# (см. left_lines), справа слот остаётся отдельным полем ввода.
# Литерал из капса без слота: |DC|, |OK| — вертикальные черты (не подчёркивания); в показе черты снимаются.
# Output (task.checker_data): тот же объём строк, на месте слотов — правильные ответы.
# Несколько допустимых вариантов в одном слоте: _КАНОН|вариант2|вариант3_
# (в показе решения — только канон до первого |, без подчёркиваний вокруг слота).
#
# Для UI right_tokens хранит не None, а токены:
#   - {'type': 'text', 'text': '...'}
#   - {'type': 'slot', 'slot_index': 0..N-1}

import json
import re
import unicodedata

# Только для колонки «условие» слева: _76_ → 76 (слоты на правой стороне не трогаем).
_LITERAL_NUMERIC_UNDERSCORE = re.compile(r'_(\d+)_')

# Слот: _непустая_ последовательность_
_SLOT_UNDERSCORE = re.compile(r'_([^_]+)_')

# Литерал «как в тексте», не слот: |DC| — не путать с _A|B_ (альтернативы только внутри _…_).
_PIPE_LITERAL = re.compile(r'\|([^|]+)\|')


def _chars_with_unicode_categories(categories):
    out = []
    for i in range(0x110000):
        if 0xD800 <= i <= 0xDFFF:
            continue
        c = chr(i)
        if unicodedata.category(c) in categories:
            out.append(c)
    return ''.join(out)


def _regex_char_class(chars):
    """Символы для вставки внутрь [...] (дефис — в начале класса)."""
    s = sorted(set(chars))
    parts = ['-'] if '-' in s else []
    s = [c for c in s if c != '-']
    for c in s:
        if c in '\\]':
            parts.append('\\' + c)
        elif c == '^':
            parts.append('\\^')
        else:
            parts.append(c)
    return ''.join(parts)


# Капс-слот: 2+ подряд Unicode Lu (включая Ë, Ä, Заглавные кириллицы и т.д.);
# границы — не примыкает к букве (Lu/Ll/Lm/Lo/Lt), чтобы не резать слова вроде iPhone.
_LETTER_CATEGORIES = frozenset({'Lu', 'Ll', 'Lm', 'Lo', 'Lt'})
_UPPER_CHARS = _chars_with_unicode_categories(frozenset({'Lu'}))
_LETTER_CHARS = _chars_with_unicode_categories(_LETTER_CATEGORIES)
_LCC = _regex_char_class(_LETTER_CHARS)
_UCC = _regex_char_class(_UPPER_CHARS)
_SLOT_CAPS = re.compile(
    '(?<![' + _LCC + '])([' + _UCC + ']{2,})(?![' + _LCC + '])'
)


def replacements_strip_literal_numeric_underscores(line):
    """Только для left_lines: _76_ → 76. _12|34_ и буквенные _КОТ_ не трогаем."""
    if not line:
        return line
    return _LITERAL_NUMERIC_UNDERSCORE.sub(r'\1', line)


def replacements_strip_pipe_literals(line):
    """|DC| → DC (и для left_lines, и для фрагментов между слотами)."""
    if not line:
        return line
    return _PIPE_LITERAL.sub(r'\1', line)


def _pipe_literal_inner_spans(line):
    """Интервалы [start, end) внутри строки, где капс-регекс не должен выделять слот."""
    spans = []
    for m in _PIPE_LITERAL.finditer(line):
        inner_start = m.start() + 1
        inner_end = m.end() - 1
        if inner_start < inner_end:
            spans.append((inner_start, inner_end))
    return spans


def _caps_match_in_pipe_literal(m_start, m_end, inner_spans):
    for a, b in inner_spans:
        if m_start >= a and m_end <= b:
            return True
    return False


def _find_slots_in_order(line):
    # Возвращает (start, end, content) в порядке появления
    slots = []
    pipe_inners = _pipe_literal_inner_spans(line)
    for m in _SLOT_UNDERSCORE.finditer(line):
        slots.append((m.start(), m.end(), m.group(1).strip()))
    for m in _SLOT_CAPS.finditer(line):
        # не считаем капс-слот внутри уже найденного _X_
        if any(s[0] < m.end() and s[1] > m.start() for s in slots):
            continue
        if _caps_match_in_pipe_literal(m.start(), m.end(), pipe_inners):
            continue
        slots.append((m.start(), m.end(), m.group(1)))
    slots.sort(key=lambda x: x[0])
    return slots


def _segments_and_slot_values(line):
    # Без снятия _цифр_: иначе _23_ не становится отдельным слотом.
    base = line
    slots = _find_slots_in_order(base)
    tokens = []
    slot_values = []
    pos = 0
    for start, end, content in slots:
        if start > pos:
            tokens.append({'type': 'text', 'text': base[pos:start]})
        slot_idx = len(slot_values)
        tokens.append({'type': 'slot', 'slot_index': slot_idx})
        slot_values.append(content)
        pos = end
    if pos < len(base):
        tokens.append({'type': 'text', 'text': base[pos:]})
    for t in tokens:
        if t['type'] == 'text':
            t['text'] = replacements_strip_pipe_literals(t['text'])
    return tokens, slot_values


def split_slot_answer_alternatives(content):
    """Разбор значения слота из checker_data: первая часть — канон (авторский ответ), все части — допустимы при проверке."""
    if content is None:
        content = ''
    parts = [p.strip() for p in str(content).split('|')]
    parts = [p for p in parts if p]
    if not parts:
        return '', ['']
    return parts[0], parts


def parse_replacements_checker_json_lines(checker_data):
    """
    checker_data в формате {"lines": [[ячейка, ...], ...]} — как в ReplacementsLinesChecker.
    Возвращает (canonical_rows, accept_rows) или None, если это не JSON-ответ.
    Не передавать такой JSON в _segments_and_slot_values: из строки JSON вытаскиваются
    случайные CAPS-токены (в т.ч. из соседних строк) и ломают ответы по строкам.
    """
    raw = (checker_data or '').strip()
    if not raw:
        return None
    try:
        jl = json.loads(raw).get('lines')
        if not isinstance(jl, list) or not jl:
            return None
        canonical_rows = []
        accept_rows = []
        for row in jl:
            if not isinstance(row, list):
                continue
            cr, ar = [], []
            for cell in row:
                cn, opts = split_slot_answer_alternatives(str(cell))
                cr.append(cn)
                ar.append(opts)
            canonical_rows.append(cr)
            accept_rows.append(ar)
        if not canonical_rows:
            return None
        return canonical_rows, accept_rows
    except (ValueError, TypeError):
        return None


def canonical_replacements_checker_line(line):
    """Строка ответа из checker_data без альтернатив после | (для показа решения).

    Слоты _такие_ в выводе не сохраняют подчёркивания — только подставленное слово
    (как для капс-слотов), чтобы текст читался как готовая фраза.
    """
    if not line:
        return ''
    base = line
    slots = _find_slots_in_order(base)
    out = []
    pos = 0
    for start, end, content in slots:
        if start > pos:
            out.append(replacements_strip_pipe_literals(base[pos:start]))
        first, _ = split_slot_answer_alternatives(content)
        out.append(first)
        pos = end
    if pos < len(base):
        out.append(replacements_strip_pipe_literals(base[pos:]))
    return ''.join(out)


def parse_replacements_lines_text(input_text, answer_text=None):
    if not input_text:
        return {'left_lines': [], 'right_tokens': [], 'answers': [], 'answer_accept': []}

    input_lines = [ln.rstrip('\r') for ln in input_text.split('\n')]
    answer_lines = []
    if answer_text is not None:
        answer_lines = [ln.rstrip('\r') for ln in answer_text.split('\n')]

    n = len(input_lines)
    while len(answer_lines) < n:
        answer_lines.append('')

    json_lines_data = (
        parse_replacements_checker_json_lines(answer_text) if answer_text else None
    )

    left_lines = []
    right_tokens = []
    answers = []
    answer_accept = []

    for i in range(n):
        line = input_lines[i]
        left_lines.append(
            replacements_strip_pipe_literals(
                replacements_strip_literal_numeric_underscores(line)
            )
        )

        tokens, hint_values = _segments_and_slot_values(line)
        right_tokens.append(tokens)
        n_slots = len(hint_values)

        line_canon = []
        line_accept = []

        if json_lines_data is not None:
            canonical_rows, json_accept_rows = json_lines_data
            if i < len(canonical_rows):
                cr = canonical_rows[i]
                ar = json_accept_rows[i]
            else:
                cr, ar = [], []
            for k in range(n_slots):
                if k < len(cr):
                    line_canon.append(cr[k])
                    line_accept.append(ar[k])
                else:
                    cn, opts = split_slot_answer_alternatives(hint_values[k])
                    line_canon.append(cn)
                    line_accept.append(opts)
        else:
            ans_line = answer_lines[i] if i < len(answer_lines) else ''
            if ans_line:
                _, answer_values = _segments_and_slot_values(ans_line)
                if len(answer_values) >= n_slots:
                    raw_slots = answer_values[:n_slots]
                else:
                    raw_slots = answer_values + hint_values[len(answer_values):]
            else:
                raw_slots = hint_values  # fallback: подсказки как "ответы" для отображения

            for k in range(n_slots):
                raw = raw_slots[k] if k < len(raw_slots) else hint_values[k]
                canon, opts = split_slot_answer_alternatives(raw)
                line_canon.append(canon)
                line_accept.append(opts)

        answers.append(line_canon)
        answer_accept.append(line_accept)

    return {'left_lines': left_lines, 'right_tokens': right_tokens, 'answers': answers, 'answer_accept': answer_accept}


def task_replacements_canonical_answer_row(task, line_index):
    """
    Канонические ответы по слотам для одной строки задания replacements_lines.
    Источник данных совпадает с ReplacementsLinesChecker._resolve_answer_rows (checker_data JSON
    или пара task.text + checker_data).
    """
    if getattr(task, 'task_type', None) != 'replacements_lines':
        return None
    try:
        line_index = int(line_index)
    except (TypeError, ValueError):
        return None
    if line_index < 0:
        return None
    raw = (getattr(task, 'checker_data', None) or '').strip()
    if raw:
        jl = parse_replacements_checker_json_lines(raw)
        if jl:
            canonical_rows, _ = jl
            if line_index < len(canonical_rows):
                return list(canonical_rows[line_index])
            return None
    pt = parse_replacements_lines_text(task.text or '', raw or None)
    rows = pt.get('answers') or []
    if line_index < len(rows):
        return list(rows[line_index])
    return None
