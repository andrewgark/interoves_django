"""Адвент-календарь: одно задание в день, номер N выходит в старт + (N−1) дней МСК.

Последний день необязателен; дни позже него не публикуются.
"""

from __future__ import annotations

from games.daily.page.section import ADVENT_SCHEDULE

ADVENT_GAME_ID = ADVENT_SCHEDULE.game_id
ADVENT_PUBLISH_START_TAG = ADVENT_SCHEDULE.publish_start_tag
ADVENT_END_DATE_TAG = ADVENT_SCHEDULE.end_date_tag
