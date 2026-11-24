"""Криптики: одна загадка в духе cryptic crossword в день.

В условии после подсказки в скобках стоит маска ответа: «(6)», «(3-4)»,
«(5, 4)». Принимается один ответ, без учёта регистра, пробелов и знаков.
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
