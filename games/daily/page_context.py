"""Shared presentation metadata for daily-game play pages."""

from games.daily.registry import get_daily_game


def build_daily_page_context(game_id, *, fallback_label=None,
                             fallback_pager_label='заданиями'):
    """Return stable labels shared by daily play-page templates."""
    definition = get_daily_game(game_id)
    label = definition.short_title if definition is not None else fallback_label
    pager_label = (
        definition.pager_label
        if definition is not None
        else fallback_pager_label
    )
    return {
        'daily_game_label': label,
        'daily_pager_aria_label': 'Переход между {}'.format(pager_label),
    }


def daily_statistics_url(game_id, number, *, enabled):
    """Build the statistics endpoint only when the game exposes it."""
    if not enabled:
        return ''
    definition = get_daily_game(game_id)
    if definition is not None and not definition.capabilities.statistics:
        return ''
    return '/daily-statistics/{}/{}/'.format(game_id, number)


def build_daily_lifecycle_context(
    game_id,
    *,
    is_daily_single_task,
    placement_number,
    placement_is_task_group,
    number_is_public,
    allow_unpublished,
    fallback_label=None,
    fallback_pager_label='заданиями',
):
    """Build the shared daily lifecycle part of a play-page context."""
    is_daily_single_task = bool(is_daily_single_task)
    return {
        'is_daily_single_task': is_daily_single_task,
        'daily_footer_enabled': is_daily_single_task,
        **build_daily_page_context(
            game_id,
            fallback_label=fallback_label,
            fallback_pager_label=fallback_pager_label,
        ),
        'daily_statistics_url': daily_statistics_url(
            game_id,
            placement_number,
            enabled=(
                is_daily_single_task
                and placement_is_task_group
                and (number_is_public or allow_unpublished)
            ),
        ),
    }
