"""Криптики: одна загадка в духе cryptic crossword в день.

В условии после подсказки в скобках стоит маска ответа: «(6)», «(3-4)»,
«(5, 4)». Принимается один ответ, без учёта регистра, пробелов и знаков.
Ответ всегда русский: поле ответа на странице пускает только кириллицу и
ровно столько букв, сколько в маске (static/js/answer_mask.js), поэтому
ответ и маска проверяются здесь, одинаково для консоли и криптиков игроков.
"""

from __future__ import annotations

import re

from games.daily.page.section import CRYPTIC_SCHEDULE

CRYPTIC_GAME_ID = CRYPTIC_SCHEDULE.game_id
CRYPTIC_PUBLISH_START_TAG = CRYPTIC_SCHEDULE.publish_start_tag

_SEPARATOR_RE = re.compile(r'[0-9A-Za-zА-Яа-яЁё]+|-|\s+')


def enumeration_for(answer: str) -> str:
    """«лесная тропа» → «6, 5», «из-за» → «2-2», «снег» → «4»."""
    parts = []
    pending_sep = ''
    for token in _SEPARATOR_RE.findall(str(answer or '').strip()):
        if token == '-':
            pending_sep = '-'
        elif token.isspace():
            pending_sep = pending_sep or ', '
        else:
            if parts:
                parts.append(pending_sep or ', ')
            parts.append(str(len(token)))
            pending_sep = ''
    return ''.join(parts)


def enumeration_is_valid(value: str) -> bool:
    return bool(re.fullmatch(r'\d+(?:(?:, ?|-)\d+)*', str(value or '').strip()))


# The answer field takes at most this many cells (static/js/answer_mask.js).
MASK_MAX_CELLS = 120


def mask_format(enumeration: str) -> str:
    """«5, 4» → «##### ####», «3-4» → «###-####»; пусто, если маска не годится.

    ``#`` — клетка буквы, как в формате поля Лесенки (raddle_masked_input.js).
    """
    enumeration = str(enumeration or '').strip()
    if not enumeration_is_valid(enumeration):
        return ''
    out = re.sub(r', ?', ' ', enumeration)
    if sum(int(n) for n in re.findall(r'\d+', out)) > MASK_MAX_CELLS:
        return ''
    return re.sub(r'\d+', lambda m: '#' * int(m.group()), out)


def task_enumeration(task) -> str:
    """Маска дня: своя из консоли или посчитанная по ответу."""
    return str((task.tags or {}).get('cryptic_enumeration') or '') or enumeration_for(task.answer or '')


def answer_mask(task) -> dict | None:
    """Поле ответа дня (``answer_masks_by_task_id``): клетки маски, только кириллица."""
    fmt = mask_format(task_enumeration(task))
    return {'format': fmt, 'script': 'cyrillic'} if fmt else None


PREVIEW_FIELDS = ('clue', 'answer', 'enumeration', 'markup', 'formula')


def cryptic_editor_preview(*, clue='', answer='', enumeration='', markup='', formula='') -> dict:
    """Живое превью в консоли и на /create_cryptic/: разбор и годность ответа с маской.

    ``answer_error`` видно сразу, а не только при сохранении или отправке:
    с маской не той длины верный ответ не ввести в поле.
    """
    from games.puzzles.cryptic.explain import editor_preview

    result = editor_preview(clue=clue, answer=answer, markup=markup, formula=formula)
    result['answer_error'] = answer_error(answer, enumeration) if str(answer or '').strip() else None
    return result


_ANSWER_RE = re.compile(r'[А-ЯЁ]+(?:[ -][А-ЯЁ]+)*')
ENUMERATION_ERROR = 'Маска ответа — числа через запятую или дефис, например 6 или 3-4 или 5, 4'


def answer_error(answer: str, enumeration: str = '') -> str | None:
    """Почему ответ с маской не годится, или None.

    Ответ — русские слова через пробел или дефис. Своя маска (``enumeration``)
    должна насчитывать столько же букв: иначе верный ответ не ввести в поле.
    """
    shown = ' '.join(str(answer or '').upper().split())
    if not _ANSWER_RE.fullmatch(shown):
        return 'В ответе могут быть только русские буквы, пробелы и дефисы'
    enumeration = str(enumeration or '').strip()
    if not enumeration:
        return None
    if not enumeration_is_valid(enumeration):
        return ENUMERATION_ERROR
    in_mask = sum(int(n) for n in re.findall(r'\d+', enumeration))
    in_answer = len(re.sub(r'[ -]', '', shown))
    if in_mask != in_answer:
        return 'В маске ({}) букв {}, а в ответе {}'.format(enumeration, in_mask, in_answer)
    return None
