"""Session-backed play mode selection."""


def session_play_mode_key(project_id):
    return 'play_mode_{}'.format(project_id or 'main')


def default_play_mode(project_id, *, sections_project_id='sections'):
    return 'personal' if project_id == sections_project_id else 'team'


def get_play_mode(request, project_id, *, sections_project_id='sections'):
    key = session_play_mode_key(project_id)
    mode = request.session.get(key)
    if mode not in ('team', 'personal'):
        mode = default_play_mode(
            project_id,
            sections_project_id=sections_project_id,
        )
    return mode, key
