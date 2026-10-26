"""URL context for project-scoped UI pages."""

from django.urls import reverse


_TEAM_URLS = (
    ('hub', 'team'),
    ('create', 'team_create'),
    ('join', 'team_join_page'),
    ('name_check', 'team_name_check'),
    ('info', 'team_info'),
    ('request_join', 'team_request_join'),
    ('join_by_password', 'team_join_by_password'),
    ('password', 'team_password'),
    ('rename', 'team_rename'),
    ('set_primary', 'team_set_primary'),
)


def project_base(
    project_id,
    *,
    main_project_id='main',
    sections_project_id='sections',
):
    """Return the URL prefix for a project, without a trailing slash."""
    project_id = (project_id or '').strip()
    if not project_id or project_id in (main_project_id, sections_project_id):
        return ''
    if '/' in project_id:
        project_id = project_id.replace('/', '')
    return '/' + project_id


def non_root_project_game_path(
    game,
    suffix='',
    *,
    main_project_id='main',
    sections_project_id='sections',
):
    """Return canonical /<project>/games/<game>/ path for non-root projects."""
    base = project_base(
        getattr(game, 'project_id', None),
        main_project_id=main_project_id,
        sections_project_id=sections_project_id,
    )
    if not base:
        return None
    return '{}/games/{}/{}'.format(base, game.id, suffix)


def project_urls_context(
    project_id,
    *,
    main_project_id='main',
    sections_project_id='sections',
):
    """Build common navigation URLs for a project-scoped template."""
    base = project_base(
        project_id,
        main_project_id=main_project_id,
        sections_project_id=sections_project_id,
    )
    return {
        'ui_project_id': project_id or main_project_id,
        'ui_project_base': base,
        'ui_project_home_url': (base + '/') or '/',
        'ui_project_games_url': (base + '/games/') if base else '/games/',
        'ui_project_team_url': (base + '/team/') if base else '/team/',
        'ui_project_profile_url': (base + '/profile/') if base else '/profile/',
        'ui_project_reports_url': (
            (base + '/profile/reports/') if base else '/profile/reports/'
        ),
        'ui_project_pay_url': (base + '/pay/') if base else '/pay/',
    }


def main_team_page_urls():
    """Return team URLs for the site-root project."""
    return {
        'ui_team_url_{}'.format(key): reverse('new_{}'.format(route))
        for key, route in _TEAM_URLS
    }


def project_team_page_urls(project_id):
    """Return team URLs scoped to a non-main project."""
    kwargs = {'project_id': project_id}
    return {
        'ui_team_url_{}'.format(key): reverse(
            'project_{}'.format(route),
            kwargs=kwargs,
        )
        for key, route in _TEAM_URLS
    }
