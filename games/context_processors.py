"""Context for main UI templates (root URLs)."""

from games.analytics import (
    consume_pending_goals,
    pending_signup_goals,
    pending_ticket_purchase_goals,
)
from django.conf import settings


def site_deploy_version(_request):
    """Expose SITE_DEPLOY_VERSION for deploy_version_check.js (HTML vs live API)."""
    v = getattr(settings, "SITE_DEPLOY_VERSION", "") or ""
    return {"site_deploy_version": str(v).strip()}


def analytics_bootstrap(request):
    counter_id = getattr(settings, 'YANDEX_METRIKA_COUNTER_ID', 0) or 0
    user = getattr(request, 'user', None)
    goals = (
        consume_pending_goals(request)
        + pending_signup_goals(user)
        + pending_ticket_purchase_goals(user)
    )
    deduped = {}
    for goal in goals:
        if isinstance(goal, dict) and goal.get('goal'):
            deduped[str(goal.get('key') or goal['goal'])] = goal
    return {
        'yandex_metrika_counter_id': int(counter_id),
        'pending_analytics_goals': list(deduped.values()),
    }


def ui_section_games(request):
    match = getattr(request, "resolver_match", None)
    if not match:
        return {}
    url_name = match.url_name or ""
    if not (
        url_name.startswith("ui_")
        or url_name.startswith("new_")
        or url_name.startswith("project_")
    ):
        return {}
    from games.section_paths import is_root_section_game
    from games.views.ui import get_section_games
    tz = 'Europe/Moscow'
    try:
        profile = getattr(request.user, 'profile', None)
        if profile and getattr(profile, 'timezone', None):
            tz = profile.timezone
    except Exception:
        pass

    kwargs = match.kwargs or {}
    game_id = kwargs.get('game_id')
    nav_desyatochki_active = False
    if url_name in ('ui_folder', 'new_folder') and kwargs.get('slug') == 'games':
        nav_desyatochki_active = True
    elif url_name in (
        'ui_main_game', 'new_main_game',
        'ui_results', 'new_results',
        'ui_tournament_results', 'new_tournament_results',
        'ui_game_progress', 'new_game_progress',
    ):
        nav_desyatochki_active = not game_id or not is_root_section_game(game_id)
    elif url_name in ('ui_task_group', 'new_task_group') and game_id:
        nav_desyatochki_active = not is_root_section_game(game_id)

    return {
        'section_games': get_section_games(request),
        'user_timezone': tz,
        'nav_desyatochki_active': nav_desyatochki_active,
    }


# Backward-compatible processor name.
new_ui_section_games = ui_section_games
