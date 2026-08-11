"""Rules, validation, and shared state helpers for Word Salad."""

import json
import re


WORD_RE = re.compile(r"[А-ЯЁA-Z]", re.IGNORECASE)
WORD_SALAD_GAME_ID = 'word_salad'


def normalize_word(value):
    """Keep only letters and normalize Ё → Е for comparisons."""
    return ''.join(WORD_RE.findall((value or '').upper())).replace('Ё', 'Е')


def parse_grid(value):
    if isinstance(value, (list, tuple)):
        cells = list(value)
    else:
        cells = re.findall(r'[А-ЯЁA-Z]', (value or '').upper())
    if len(cells) != 16:
        raise ValueError('Сетка должна содержать ровно 16 букв (4×4).')
    return [str(c).replace('Ё', 'Е').upper() for c in cells]


def parse_words(value):
    if isinstance(value, (list, tuple)):
        words = [str(w).strip() for w in value]
    else:
        words = [line.strip() for line in (value or '').splitlines() if line.strip()]
    if not words:
        raise ValueError('Добавьте хотя бы одно слово.')
    normalized = [normalize_word(word) for word in words]
    if any(not word for word in normalized):
        raise ValueError('Каждая строка слов должна содержать буквы.')
    if len(set(normalized)) != len(normalized):
        raise ValueError('Загаданные слова не должны повторяться.')
    return words


def default_state():
    return {'solved_indices': [], 'hints': [], 'active': list(range(16))}


def _normalized_index_list(values, *, limit):
    result = []
    for value in values or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < limit:
            result.append(index)
    return sorted(set(result))


def load_state(raw):
    state = default_state()
    if not raw:
        return state
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return state
    if not isinstance(data, dict):
        return state
    state['solved_indices'] = _normalized_index_list(data.get('solved_indices') or data.get('solved'), limit=16)
    state['hints'] = _normalized_index_list(data.get('hints'), limit=16)
    active_raw = data.get('active')
    if active_raw is None:
        state['active'] = list(range(16))
    else:
        state['active'] = _normalized_index_list(active_raw, limit=16)
    return state


def dump_state(state):
    state = load_state(state)
    return json.dumps(state, ensure_ascii=False)


def neighbours(index):
    row, col = divmod(index, 4)
    for other in range(16):
        other_row, other_col = divmod(other, 4)
        if other != index and max(abs(row - other_row), abs(col - other_col)) <= 1:
            yield other


def find_paths(grid, word, active=None, limit=1):
    """Return up to ``limit`` no-reuse Boggle paths for a normalized word."""
    target = normalize_word(word)
    active = set(range(16)) if active is None else set(active)
    result = []

    def visit(index, position, used, path):
        if len(result) >= limit:
            return
        if grid[index] != target[position]:
            return
        used = used | {index}
        path = path + [index]
        if position == len(target) - 1:
            result.append(path)
            return
        for nxt in neighbours(index):
            if nxt in active and nxt not in used:
                visit(nxt, position + 1, used, path)

    if not target:
        return result
    for index in sorted(active):
        visit(index, 0, set(), [])
        if len(result) >= limit:
            break
    return result


def all_words_solvable(grid, words, active):
    return all(find_paths(grid, word, active=active, limit=1) for word in words)


def validate_puzzle(grid_value, words_value):
    grid = parse_grid(grid_value)
    words = parse_words(words_value)
    if not all_words_solvable(grid, words, range(16)):
        raise ValueError('Для каждого слова должна существовать хотя бы одна дорожка.')
    for cell in range(16):
        active = set(range(16)) - {cell}
        if all_words_solvable(grid, words, active):
            raise ValueError('Букву в клетке {} можно убрать уже в начальной сетке.'.format(cell + 1))
    return grid, words


def removable_cells(grid, words, active, excluded_words=()):
    excluded = set(excluded_words)
    remaining = [word for i, word in enumerate(words) if i not in excluded]
    removable = []
    for cell in sorted(active):
        candidate = set(active) - {cell}
        if all_words_solvable(grid, remaining, candidate):
            removable.append(cell)
    return removable


def mask_for_word(word, reveal_first=False):
    normalized = normalize_word(word)
    letters = iter(normalized)
    revealed = False
    result = []
    for ch in word.upper():
        if ch.isalpha():
            letter = next(letters)
            if reveal_first and not revealed:
                result.append(letter)
                revealed = True
            else:
                result.append('_')
        else:
            result.append(ch)
    return ''.join(result)


def parse_task_data(checker_data, answer):
    try:
        data = json.loads(checker_data or '{}')
    except (TypeError, ValueError):
        raise ValueError('checker_data должен быть JSON с полями grid и words.')
    grid = parse_grid(data.get('grid'))
    words_raw = data.get('words') if data.get('words') is not None else answer
    words = parse_words(words_raw)
    return grid, words


def validate_task_data(checker_data, answer):
    grid, words = parse_task_data(checker_data, answer)
    return validate_puzzle(grid, words)


def format_grid_text(grid):
    grid = parse_grid(grid)
    return '\n'.join(' '.join(grid[row * 4:(row + 1) * 4]) for row in range(4))


def format_words_text(words):
    return '\n'.join(parse_words(words))


def serialize_task_data(grid_value, words_value):
    grid = parse_grid(grid_value)
    words = parse_words(words_value)
    return json.dumps({'grid': grid, 'words': words}, ensure_ascii=False)


def build_ui_context(grid, words, state=None):
    state = load_state(state)
    solved = set(state.get('solved_indices') or [])
    hints = set(state.get('hints') or [])
    active = set(state.get('active', []))
    hinted_letters = {
        normalize_word(words[index])[:1]
        for index in hints
        if 0 <= index < len(words) and normalize_word(words[index])
    }
    grid_rows = []
    for row_index in range(4):
        row = []
        for col_index in range(4):
            index = row_index * 4 + col_index
            letter = grid[index]
            row.append({
                'index': index,
                'letter': letter,
                'is_active': index in active,
                'is_hinted': letter in hinted_letters,
            })
        grid_rows.append(row)

    words_ui = []
    for index, word in sorted(
        enumerate(words),
        key=lambda item: (len(normalize_word(item[1])), normalize_word(item[1]), item[0]),
    ):
        normalized = normalize_word(word)
        first_letter = normalized[:1]
        words_ui.append({
            'index': index,
            'original': word,
            'mask_html': word if index in solved else mask_for_word(word, reveal_first=index in hints),
            'length': len(normalized),
            'is_solved': index in solved,
            'is_hinted': index in hints,
            'first_letter': first_letter,
            'first_letter_cells': [i for i, letter in enumerate(grid) if letter == first_letter],
        })

    return {
        'grid_rows': grid_rows,
        'words': words_ui,
        'solved_indices': sorted(solved),
        'hints': sorted(hints),
        'active': sorted(active),
        'is_complete': len(solved) == len(words),
        'words_total': len(words),
    }
