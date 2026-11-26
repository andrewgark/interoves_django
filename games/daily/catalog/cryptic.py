"""Криптики: одна загадка в духе cryptic crossword в день.

В условии после подсказки в скобках стоит маска ответа: «(6)», «(3-4)»,
«(5, 4)». Принимается один ответ, без учёта регистра, пробелов и знаков.
Ответ всегда русский: поле ответа на странице пускает только кириллицу и
ровно столько букв, сколько в маске (static/js/cryptic_play.js), поэтому
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
