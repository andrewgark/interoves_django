"""Canonical URLs for task-group navigation."""


def play_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/'.format(project_base, game.id, number)
    from games.section_paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return section_play_path(game.id, number)
    return '/games/{}/{}/'.format(game.id, number)


def results_url_for_task_group(game, number, *, project_base=''):
    """Canonical results URL for one task group, alongside its play URL."""
    if project_base:
        return '{}/games/{}/{}/results/'.format(project_base, game.id, number)
    from games.section_paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return '{}results/'.format(section_play_path(game.id, number))
    return '/games/{}/{}/results/'.format(game.id, number)


def replay_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/replay/'.format(project_base, game.id, number)
    from games.section_paths import section_replay_path
    return section_replay_path(game.id, number)


def replay_exit_url_for_task_group(game, number, *, project_base=''):
    return replay_url_for_task_group(
        game,
        number,
        project_base=project_base,
    ).rstrip('/') + '/exit/'
