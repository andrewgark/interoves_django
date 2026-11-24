"""Простые ежедневные игры: адвент-календарь и криптики.

Каждый день — одна группа с одним обычным заданием (HTML-условие и ответ).
Настройки раздела лежат на игре: название в ``Game.name`` /
``Game.outside_name``, остальное в ``Game.tags``.
"""

from __future__ import annotations

from games.daily.page.section import ADVENT_SCHEDULE, CRYPTIC_SCHEDULE, BoundedDailySchedule

SIMPLE_DAILY_SCHEDULES: dict[str, BoundedDailySchedule] = {
    ADVENT_SCHEDULE.game_id: ADVENT_SCHEDULE,
    CRYPTIC_SCHEDULE.game_id: CRYPTIC_SCHEDULE,
}
# Generic tag read by the section pages for their tagline.
SECTION_DESCRIPTION_TAG = 'section_description'
# Кнопка «ответ» на странице дня: по умолчанию выключена.
SHOW_ANSWER_TAG = 'show_answer_button'


def is_simple_daily_game(game_id) -> bool:
    return str(game_id or '') in SIMPLE_DAILY_SCHEDULES


def day_count(game) -> int | None:
    """How many days fit between the first and the last day, if both are set."""
    schedule = SIMPLE_DAILY_SCHEDULES.get(getattr(game, 'id', None))
    if schedule is None:
        return None
    start = schedule.publish_start(game)
    end = schedule.end_date(game)
    if start is None or end is None:
        return None
    return max(0, (end - start.date()).days + 1)


def answer_button_hidden(game) -> bool:
    """Простые ежедневные игры прячут «ответ», пока его не включили в настройках."""
    if not is_simple_daily_game(getattr(game, 'id', None)):
        return False
    return not bool((getattr(game, 'tags', None) or {}).get(SHOW_ANSWER_TAG))
