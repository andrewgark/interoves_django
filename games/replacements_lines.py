# Парсинг заданий типа "Замены" (replacements_lines).
#
# Input: текст, где слоты — (1) слова 2+ букв капсом (2+ заглавных букв подряд),
#                         (2) слова в _таком_ виде.
# Output (task.checker_data): тот же объём строк, на месте слотов — правильные ответы.
#
# Для UI right_tokens хранит не None, а токены:
#   - {'type': 'text', 'text': '...'}
#   - {'type': 'slot', 'slot_index': 0..N-1}

import re

# Слот: _непустая_ последовательность_
_SLOT_UNDERSCORE = re.compile(r'_([^_]+)_')
# Капс: 2+ заглавных буквы (A-Z, А-Я, Ё) с границами слова по буквам
_SLOT_CAPS = re.compile(r'(?<![A-Za-zА-Яа-яЁё])([A-ZА-ЯЁ]{2,})(?![A-Za-zА-Яа-яЁё])')


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
    slots = _find_slots_in_order(line)
    tokens = []
    slot_values = []
    pos = 0
    for start, end, content in slots:
        if start > pos:
            tokens.append({'type': 'text', 'text': line[pos:start]})
        slot_idx = len(slot_values)
        tokens.append({'type': 'slot', 'slot_index': slot_idx})
        slot_values.append(content)
        pos = end
    if pos < len(line):
        tokens.append({'type': 'text', 'text': line[pos:]})
    return tokens, slot_values


def parse_replacements_lines_text(input_text, answer_text=None):
    if not input_text:
        return {'left_lines': [], 'right_tokens': [], 'answers': []}

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

    for i in range(n):
        line = input_lines[i]
        left_lines.append(line)

        tokens, hint_values = _segments_and_slot_values(line)
        right_tokens.append(tokens)
        n_slots = len(hint_values)

        ans_line = answer_lines[i] if i < len(answer_lines) else ''
        if ans_line:
            _, answer_values = _segments_and_slot_values(ans_line)
            if len(answer_values) >= n_slots:
                answers.append(answer_values[:n_slots])
            else:
                answers.append(answer_values + hint_values[len(answer_values):])
        else:
            answers.append(hint_values)  # fallback: подсказки как "ответы" для отображения

    return {'left_lines': left_lines, 'right_tokens': right_tokens, 'answers': answers}
