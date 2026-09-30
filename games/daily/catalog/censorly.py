# Ежедневная Цензурка: номер = 1, 2, 3…
# Дата публикации N = Game.tags['censorly_publish_start'] + (N−1) дней (МСК).

from __future__ import annotations

from games.daily.section import CENSORLY_SCHEDULE, MOSCOW

CENSORLY_GAME_ID = CENSORLY_SCHEDULE.game_id
CENSORLY_PUBLISH_START_TAG = CENSORLY_SCHEDULE.publish_start_tag
CENSORLY_BUFFER_DAYS = 14

censorly_publish_start = CENSORLY_SCHEDULE.publish_start
censorly_number_for_date = CENSORLY_SCHEDULE.number_for_date
current_censorly_number = CENSORLY_SCHEDULE.current_number
censorly_publish_at = CENSORLY_SCHEDULE.publish_at
is_censorly_number_published = CENSORLY_SCHEDULE.is_published
filter_published_censorly_links = CENSORLY_SCHEDULE.filter_published
visible_censorly_links = CENSORLY_SCHEDULE.visible_links
get_censorly_hub_context = CENSORLY_SCHEDULE.hub_context
