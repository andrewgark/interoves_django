# Ежедневные лесенки (раздел «Лесенка»): номер круга = 1, 2, 3…
# Дата публикации N-й лесенки: Game.tags['ladder_publish_start'] + (N-1) дней (МСК).

from __future__ import annotations

from django.utils import timezone

from games.daily_section import LADDER_SCHEDULE, MOSCOW

LADDER_GAME_ID = LADDER_SCHEDULE.game_id
LADDER_PUBLISH_START_TAG = LADDER_SCHEDULE.publish_start_tag

ladder_publish_start = LADDER_SCHEDULE.publish_start
ladder_number_for_date = LADDER_SCHEDULE.number_for_date
current_ladder_number = LADDER_SCHEDULE.current_number
ladder_publish_at = LADDER_SCHEDULE.publish_at
is_ladder_number_published = LADDER_SCHEDULE.is_published
filter_published_ladder_links = LADDER_SCHEDULE.filter_published
visible_ladder_links = LADDER_SCHEDULE.visible_links
get_ladder_hub_context = LADDER_SCHEDULE.hub_context


def sort_ladder_links_newest_first(links):
    if hasattr(links, 'filter'):
        from games.models import GameTaskGroup
        return GameTaskGroup.order_queryset_by_number(links, reverse=True)
    return sorted(links, key=lambda link: link.key_sort(), reverse=True)
