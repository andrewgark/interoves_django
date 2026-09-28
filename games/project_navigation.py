"""URL context for project-scoped UI pages."""


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
