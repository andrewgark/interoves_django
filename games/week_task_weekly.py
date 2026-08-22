# Еженедельное «Задание недели»: номер = 1, 2, 3…
# Дата публикации N = Game.tags['week_task_publish_start'] + (N−1)*7 дней (понедельник 00:00 МСК).

from __future__ import annotations

from django.utils import timezone

from games.daily_section import MOSCOW, WEEK_TASK_SCHEDULE

WEEK_TASK_GAME_ID = WEEK_TASK_SCHEDULE.game_id
WEEK_TASK_PUBLISH_START_TAG = WEEK_TASK_SCHEDULE.publish_start_tag
WEEK_TASK_BUFFER_WEEKS = 8

week_task_publish_start = WEEK_TASK_SCHEDULE.publish_start
week_task_number_for_date = WEEK_TASK_SCHEDULE.number_for_date
current_week_task_number = WEEK_TASK_SCHEDULE.current_number
week_task_publish_at = WEEK_TASK_SCHEDULE.publish_at
is_week_task_number_published = WEEK_TASK_SCHEDULE.is_published
filter_published_week_task_links = WEEK_TASK_SCHEDULE.filter_published
visible_week_task_links = WEEK_TASK_SCHEDULE.visible_links
get_week_task_hub_context = WEEK_TASK_SCHEDULE.hub_context
