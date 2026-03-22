# Парсинг заданий типа "Замены" (replacements_lines).
#
# Input: текст, где слоты — (1) слова из 2+ подряд символов Unicode Lu (ALL CAPS, в т.ч. Ë, Ä, А-Я),
#                         (2) слова в _таком_ виде.
# Фрагменты вида _только_цифры_ (например _76_) — не слоты: в тексте показываются как 76.
# Output (task.checker_data): тот же объём строк, на месте слотов — правильные ответы.
# Несколько допустимых вариантов в одном слоте: _КАНОН|вариант2|вариант3_
# (в показе решения — только канон до первого |, без подчёркиваний вокруг слота).
#
# Для UI right_tokens хранит не None, а токены:
#   - {'type': 'text', 'text': '...'}
#   - {'type': 'slot', 'slot_index': 0..N-1}

import re
import unicodedata

# Литеральное число в подчёркиваниях — не слот (отображается без _)
_LITERAL_NUMERIC_UNDERSCORE = re.compile(r'_(\d+)_')

# Слот: _непустая_ последовательность_
_SLOT_UNDERSCORE = re.compile(r'_([^_]+)_')


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
    """_76_ → 76; слоты с буквами и _12|34_ не трогаем."""
    if not line:
        return line
    return _LITERAL_NUMERIC_UNDERSCORE.sub(r'\1', line)


def _find_slots_in_order(line):
    # Возвращает (start, end, content) в порядке появления
    slots = []
    for m in _SLOT_UNDERSCORE.finditer(line):
        slots.append((m.start(), m.end(), m.group(1).strip()))
    for m in _SLOT_CAPS.finditer(line):
        # не считаем капс-слот внутри уже найденного _X_
        if not any(s[0] < m.end() and s[1] > m.start() for s in slots):
            slots.append((m.start(), m.end(), m.group(1)))
    slots.sort(key=lambda x: x[0])
    return slots


def _segments_and_slot_values(line):
    base = replacements_strip_literal_numeric_underscores(line)
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


def canonical_replacements_checker_line(line):
    """Строка ответа из checker_data без альтернатив после | (для показа решения).

    Слоты _такие_ в выводе не сохраняют подчёркивания — только подставленное слово
    (как для капс-слотов), чтобы текст читался как готовая фраза.
    """
    if not line:
        return ''
    base = replacements_strip_literal_numeric_underscores(line)
    slots = _find_slots_in_order(base)
    out = []
    pos = 0
    for start, end, content in slots:
        if start > pos:
            out.append(base[pos:start])
        first, _ = split_slot_answer_alternatives(content)
        out.append(first)
        pos = end
    if pos < len(base):
        out.append(base[pos:])
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

    left_lines = []
    right_tokens = []
    answers = []
    answer_accept = []

    for i in range(n):
        line = input_lines[i]
        left_lines.append(replacements_strip_literal_numeric_underscores(line))

        tokens, hint_values = _segments_and_slot_values(line)
        right_tokens.append(tokens)
        n_slots = len(hint_values)

        ans_line = answer_lines[i] if i < len(answer_lines) else ''
        if ans_line:
            _, answer_values = _segments_and_slot_values(ans_line)
            if len(answer_values) >= n_slots:
                raw_slots = answer_values[:n_slots]
            else:
                raw_slots = answer_values + hint_values[len(answer_values):]
        else:
            raw_slots = hint_values  # fallback: подсказки как "ответы" для отображения

        line_canon = []
        line_accept = []
        for k in range(n_slots):
            raw = raw_slots[k] if k < len(raw_slots) else hint_values[k]
            canon, opts = split_slot_answer_alternatives(raw)
            line_canon.append(canon)
            line_accept.append(opts)
        answers.append(line_canon)
        answer_accept.append(line_accept)

    return {'left_lines': left_lines, 'right_tokens': right_tokens, 'answers': answers, 'answer_accept': answer_accept}
