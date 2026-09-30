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
