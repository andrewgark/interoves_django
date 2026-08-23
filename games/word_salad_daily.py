# Ежедневный Салатик: номер = 1, 2, 3…
# Дата публикации N = Game.tags['word_salad_publish_start'] + (N−1) дней (МСК).

from __future__ import annotations

from django.utils import timezone

from games.daily_section import MOSCOW, WORD_SALAD_SCHEDULE

WORD_SALAD_PUBLISH_START_TAG = WORD_SALAD_SCHEDULE.publish_start_tag
WORD_SALAD_DEFAULT_PUBLISH_START = '2026-08-23T00:00:00+03:00'

word_salad_publish_start = WORD_SALAD_SCHEDULE.publish_start
word_salad_number_for_date = WORD_SALAD_SCHEDULE.number_for_date
current_word_salad_number = WORD_SALAD_SCHEDULE.current_number
word_salad_publish_at = WORD_SALAD_SCHEDULE.publish_at
is_word_salad_number_published = WORD_SALAD_SCHEDULE.is_published
filter_published_word_salad_links = WORD_SALAD_SCHEDULE.filter_published
visible_word_salad_links = WORD_SALAD_SCHEDULE.visible_links
get_word_salad_hub_context = WORD_SALAD_SCHEDULE.hub_context
