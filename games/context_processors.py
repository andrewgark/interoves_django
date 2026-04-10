"""Context for main UI templates (root URLs)."""

from django.conf import settings


def site_deploy_version(_request):
    """Expose SITE_DEPLOY_VERSION for deploy_version_check.js (HTML vs live API)."""
    v = getattr(settings, "SITE_DEPLOY_VERSION", "") or ""
    return {"site_deploy_version": str(v).strip()}


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
    from games.views.ui import get_section_games
    tz = 'Europe/Moscow'
    try:
        profile = getattr(request.user, 'profile', None)
        if profile and getattr(profile, 'timezone', None):
            tz = profile.timezone
    except Exception:
        pass
    return {
        'section_games': get_section_games(request),
        'user_timezone': tz,
    }


# Backward-compatible processor name.
new_ui_section_games = ui_section_games
