"""Rules, validation, and shared state helpers for Word Salad."""

import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from games.share_result import elapsed_label_from_attempts

_EXTRA_NOUNS_PATH = Path(__file__).resolve().parent / 'word_salad_nouns.txt'

WORD_RE = re.compile(r"[А-ЯЁA-Z]", re.IGNORECASE)
_TITLE_RE = re.compile(r'^(?:Словесный\s+)?Салат(?:ик)?\s*#\s*\d+$', re.IGNORECASE)
WORD_SALAD_GAME_ID = 'salad'
WORD_POINTS = Decimal('1')
HINT_PENALTY = Decimal('0.5')
EXTRA_MIN_LENGTH = 3
EXTRA_NOT_FOUND_COMMENT = 'Слово не найдено'
NO_HINT_SQUARE = '🟩'
OVERFLOW_HINT_SQUARE = '*️⃣'
_HINT_KEYCAPS = ('1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟')


def normalize_word(value):
    """Keep only letters and normalize Ё → Е for comparisons."""
    return ''.join(WORD_RE.findall((value or '').upper())).replace('Ё', 'Е')


def parse_grid(value):
    if isinstance(value, (list, tuple)):
        cells = []
        for raw_cell in value:
            letters = WORD_RE.findall(str(raw_cell or '').upper())
            if len(letters) != 1:
                raise ValueError('Каждая клетка сетки должна содержать ровно одну букву.')
            cells.append(letters[0])
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
    return {
        'solved_indices': [],
        'hints': [],
        'hint_counts': {},
        'active': list(range(16)),
    }


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
    hint_counts = {}
    raw_counts = data.get('hint_counts')
    if isinstance(raw_counts, dict):
        for raw_index, raw_count in raw_counts.items():
            try:
                index = int(raw_index)
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if index >= 0 and count > 0:
                hint_counts[index] = count
    for index in _normalized_index_list(data.get('hints'), limit=16):
        hint_counts[index] = max(1, hint_counts.get(index, 0))
    state['hint_counts'] = hint_counts
    state['hints'] = sorted(hint_counts)
    active_raw = data.get('active')
    if active_raw is None:
        state['active'] = list(range(16))
    else:
        state['active'] = _normalized_index_list(active_raw, limit=16)
    return state


def dump_state(state):
    state = load_state(state)
    return json.dumps(state, ensure_ascii=False)


def score_for_state(raw_state):
    """Net Word Salad score: one point per word, half a point per letter hint."""
    state = load_state(raw_state)
    solved = len(state.get('solved_indices') or [])
    hints = sum(int(count or 0) for count in (state.get('hint_counts') or {}).values())
    return max(Decimal('0'), WORD_POINTS * solved - HINT_PENALTY * hints)


def result_square_for_hint_count(count):
    """🟩 if the word was solved without hints; otherwise a keycap for the hint count."""
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return NO_HINT_SQUARE
    if n > 10:
        return OVERFLOW_HINT_SQUARE
    return _HINT_KEYCAPS[n - 1]


def words_in_display_order(words):
    return sorted(enumerate(words), key=lambda item: (normalize_word(item[1]), item[0]))


def result_squares_for_state(words, state):
    """Share squares in answer-list (alphabetical) order; empty until the salad is complete."""
    if not words:
        return ''
    state = load_state(state)
    solved = set(state.get('solved_indices') or [])
    if len(solved) < len(words):
        return ''
    hint_counts = state.get('hint_counts') or {}
    return ''.join(
        result_square_for_hint_count(hint_counts.get(index, 0))
        for index, _word in words_in_display_order(words)
    )


def salad_hub_result_for_actor(
    task,
    *,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    game=None,
    include_other_games=False,
    attempts_info=None,
):
    """Квадраты и время для списка салатиков. Пусто, пока салатик не собран целиком."""
    from games.models import Attempt

    try:
        _grid, words = parse_task_data(getattr(task, 'checker_data', None), getattr(task, 'answer', None) or '')
    except (TypeError, ValueError):
        return '', None
    game_arg = None if include_other_games else game
    ai = attempts_info
    if ai is None:
        ai = Attempt.manager.get_attempts_info(
            team=team,
            task=task,
            mode=mode,
            user=user,
            anon_key=anon_key,
            game=game_arg,
        )
    if not ai.attempts:
        return '', None
    state = default_state()
    for attempt in reversed(ai.attempts):
        if attempt.state:
            state = load_state(attempt.state)
            break
    squares = result_squares_for_state(words, state)
    if not squares:
        return '', None
    return squares, elapsed_label_from_attempts(ai.attempts)


def result_points_from_attempts(attempts):
    if not attempts:
        return Decimal('0')
    return score_for_state(getattr(attempts[-1], 'state', None))


def hint_numbers_from_attempts(attempts):
    """Return result-table labels in the same alphabetical order as the answer list."""
    if not attempts:
        return []
    latest = attempts[-1]
    task = getattr(latest, 'task', None)
    if task is None:
        return []
    try:
        _, words = parse_task_data(task.checker_data, '')
    except (AttributeError, ValueError):
        return []
    display_numbers = {
        original_index: display_index
        for display_index, (original_index, _word) in enumerate(
            words_in_display_order(words),
            start=1,
        )
    }
    hint_counts = load_state(getattr(latest, 'state', None)).get('hint_counts') or {}
    labels = []
    for original_index, count in hint_counts.items():
        display_number = display_numbers.get(int(original_index))
        if display_number is None:
            continue
        labels.extend('{}.{}'.format(display_number, hint_number) for hint_number in range(1, int(count) + 1))
    return labels


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


def mask_for_word(word, reveal_count=0, reveal_first=False):
    normalized = normalize_word(word)
    letters = iter(normalized)
    if reveal_first:
        reveal_count = max(1, reveal_count)
    reveal_count = max(0, int(reveal_count or 0))
    letter_index = 0
    result = []
    for ch in word.upper():
        if ch.isalpha():
            letter = next(letters)
            if letter_index < reveal_count:
                result.append(letter)
            else:
                result.append('⬜')
            letter_index += 1
        else:
            result.append(ch)
    return ''.join(result)


def ru_count_label(n, one, few, many):
    """Russian plural: 1 слово, 2 слова, 5 слов."""
    n = int(n)
    absn = abs(n)
    if absn % 10 == 1 and absn % 100 != 11:
        form = one
    elif absn % 10 in (2, 3, 4) and absn % 100 not in (12, 13, 14):
        form = few
    else:
        form = many
    return '{} {}'.format(n, form)


@lru_cache(maxsize=1)
def load_extra_noun_set():
    """Nominative Russian nouns for salad extras — not the alphabetty word-form dict."""
    if not _EXTRA_NOUNS_PATH.is_file():
        return frozenset()
    words = set()
    with _EXTRA_NOUNS_PATH.open(encoding='utf-8') as handle:
        for line in handle:
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            word = normalize_word(raw)
            if EXTRA_MIN_LENGTH <= len(word) <= 16:
                words.add(word)
    return frozenset(words)


def extra_found_word(written, puzzle_words, *, user=None, anon_key=None):
    """Return a noun from the salad extras dict that is not a puzzle answer, else ''."""
    word = normalize_word(written)
    if len(word) < EXTRA_MIN_LENGTH:
        return ''
    answers = {normalize_word(item) for item in (puzzle_words or [])}
    if word in answers:
        return ''
    if word in load_extra_noun_set():
        return word
    return ''


def extra_found_word_from_attempt(task, attempt, *, user=None, anon_key=None):
    """Dictionary extra for a rejected salad path, else ''."""
    if (getattr(attempt, 'comment', None) or '') != EXTRA_NOT_FOUND_COMMENT:
        return ''
    try:
        payload = json.loads(attempt.text or '')
        path = [int(index) for index in (payload.get('path') or [])]
        grid, words = parse_task_data(getattr(task, 'checker_data', None), '')
        written = ''.join(grid[index] for index in path)
    except (TypeError, ValueError, IndexError, KeyError, json.JSONDecodeError):
        return ''
    return extra_found_word(written, words, user=user, anon_key=anon_key)


def theme_from_text(value):
    """Theme value for display: drop title leftovers and a leading «Тема:»."""
    theme = (value or '').strip()
    if not theme or _TITLE_RE.match(theme):
        return ''
    theme = ' '.join(theme.split())
    if theme.lower().startswith('тема:'):
        theme = theme[5:].strip()
    return theme


def archive_card_meta(task):
    """Theme and word count for the public salad list, e.g. «Города России · 6 слов»."""
    try:
        _, words = parse_task_data(getattr(task, 'checker_data', None), '')
        n_words = len(words)
    except Exception:
        n_words = 0
    theme = theme_from_text(getattr(task, 'text', None))
    words_label = ru_count_label(n_words, 'слово', 'слова', 'слов') if n_words else ''
    if theme and words_label:
        return '{} · {}'.format(theme, words_label)
    return theme or words_label or None


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


def build_ui_context(grid, words, state=None, attempts=None):
    state = load_state(state)
    solved = set(state.get('solved_indices') or [])
    hint_counts = state.get('hint_counts') or {}
    active = set(state.get('active', []))
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
            })
        grid_rows.append(row)

    words_ui = []
    for index, word in words_in_display_order(words):
        normalized = normalize_word(word)
        hint_count = min(len(normalized), int(hint_counts.get(index, 0) or 0))
        mask = word if index in solved else mask_for_word(word, reveal_count=hint_count)
        words_ui.append({
            'index': index,
            'original': word,
            'normalized': normalized,
            'mask_html': mask,
            'mask_chars': list(mask),
            'length': len(normalized),
            'is_solved': index in solved,
            'is_hinted': hint_count > 0,
            'hint_count': hint_count,
            'next_hint_number': hint_count + 1,
            'can_hint': index not in solved and hint_count < len(normalized),
        })

    return {
        'grid_rows': grid_rows,
        'words': words_ui,
        'word_points': WORD_POINTS,
        'hint_penalty': HINT_PENALTY,
        'solved_indices': sorted(solved),
        'hints': sorted(index for index, count in hint_counts.items() if count > 0),
        'hint_counts': hint_counts,
        'active': sorted(active),
        'is_complete': len(solved) == len(words),
        'words_total': len(words),
        'result_squares': result_squares_for_state(words, state),
        'elapsed_label': elapsed_label_from_attempts(attempts) if len(solved) == len(words) else '',
    }
