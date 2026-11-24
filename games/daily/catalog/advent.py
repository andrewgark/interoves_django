"""Адвент-календарь: одно задание в день, номер N выходит в старт + (N−1) дней МСК.

Настройки раздела живут на самой игре: название в ``Game.name`` /
``Game.outside_name``, всё остальное в ``Game.tags``. Последний день
необязателен; дни позже него не публикуются.
"""

from __future__ import annotations

from games.daily.page.section import ADVENT_SCHEDULE

ADVENT_GAME_ID = ADVENT_SCHEDULE.game_id
ADVENT_PUBLISH_START_TAG = ADVENT_SCHEDULE.publish_start_tag
ADVENT_END_DATE_TAG = ADVENT_SCHEDULE.end_date_tag
# Кнопка «ответ» на странице дня: по умолчанию выключена.
ADVENT_SHOW_ANSWER_TAG = 'advent_show_answer'
# Generic tag read by the section pages for their tagline.
SECTION_DESCRIPTION_TAG = 'section_description'
ADVENT_CHECKER_ID = 'equals_with_possible_spaces'
ADVENT_DEFAULT_TITLE = 'Адвент-календарь'

advent_publish_start = ADVENT_SCHEDULE.publish_start
current_advent_number = ADVENT_SCHEDULE.current_number
advent_publish_at = ADVENT_SCHEDULE.publish_at
is_advent_number_published = ADVENT_SCHEDULE.is_published
advent_end_date = ADVENT_SCHEDULE.end_date


def advent_day_count(game) -> int | None:
    """How many days fit between the first and the last day, if both are set."""
    start = advent_publish_start(game)
    end = advent_end_date(game)
    if start is None or end is None:
        return None
    return max(0, (end - start.date()).days + 1)


def answer_button_hidden(game) -> bool:
    """Адвент прячет кнопку «ответ» (и сам ответ), пока её не включили в настройках."""
    if getattr(game, 'id', None) != ADVENT_GAME_ID:
        return False
    return not bool((getattr(game, 'tags', None) or {}).get(ADVENT_SHOW_ANSWER_TAG))
