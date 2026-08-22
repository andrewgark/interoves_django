"""Parse Word Salad / Салат puzzles from the authoring spreadsheet CSV."""

from __future__ import annotations

import csv
import io
import re

LETTER_RE = re.compile(r'[А-ЯЁA-Z]', re.IGNORECASE)


def _cell_letter(raw: str) -> str:
    letters = LETTER_RE.findall(raw or '')
    if not letters:
        return ''
    return letters[0].upper().replace('Ё', 'Е')


def parse_word_salad_csv(csv_text: str) -> dict[int, dict]:
    """Return {number: {'grid': [16 letters], 'words': [...], 'theme': str}}."""
    reader = csv.reader(io.StringIO(csv_text))
    next(reader, None)

    salads: dict[int, dict] = {}
    current: int | None = None
    grid_rows: list[list[str]] = []
    words: list[str] = []
    theme = ''

    def flush():
        nonlocal current, grid_rows, words, theme
        if current is None:
            return
        grid = [letter for row in grid_rows for letter in row]
        salads[current] = {
            'grid': grid,
            'words': list(words),
            'theme': theme.strip(),
        }
        current = None
        grid_rows = []
        words = []
        theme = ''

    for row in reader:
        if not row or not any((cell or '').strip() for cell in row):
            continue
        number_raw = (row[0] if row else '').strip()
        if number_raw.isdigit():
            flush()
            current = int(number_raw)

        if current is None:
            continue

        letters = [_cell_letter(row[i] if len(row) > i else '') for i in range(2, 6)]
        if all(letters):
            grid_rows.append(letters)

        word = (row[7] if len(row) > 7 else '').strip()
        if word:
            words.append(word)

        theme_cell = (row[8] if len(row) > 8 else '').strip()
        if theme_cell and not theme:
            theme = theme_cell

    flush()
    return salads
