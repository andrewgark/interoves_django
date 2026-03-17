"""Context for /new/ templates (e.g. nav section games)."""


def new_ui_section_games(request):
    if not request.path.startswith('/new/'):
        return {}
    from games.views.new_ui import get_section_games
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
