# Ежедневная Алфавитка: номер = 1, 2, 3…
# Дата публикации N = Game.tags['alphabetty_publish_start'] + (N−1) дней (МСК).

from __future__ import annotations

from django.utils import timezone

from games.daily_section import ALPHABETTY_SCHEDULE, MOSCOW

ALPHABETTY_GAME_ID = ALPHABETTY_SCHEDULE.game_id
ALPHABETTY_PUBLISH_START_TAG = ALPHABETTY_SCHEDULE.publish_start_tag
ALPHABETTY_BUFFER_DAYS = 30

alphabetty_publish_start = ALPHABETTY_SCHEDULE.publish_start
alphabetty_number_for_date = ALPHABETTY_SCHEDULE.number_for_date
current_alphabetty_number = ALPHABETTY_SCHEDULE.current_number
alphabetty_publish_at = ALPHABETTY_SCHEDULE.publish_at
is_alphabetty_number_published = ALPHABETTY_SCHEDULE.is_published
filter_published_alphabetty_links = ALPHABETTY_SCHEDULE.filter_published
visible_alphabetty_links = ALPHABETTY_SCHEDULE.visible_links
get_alphabetty_hub_context = ALPHABETTY_SCHEDULE.hub_context
