"""Canonical URLs for task-group navigation."""

from django.utils.html import strip_tags

from games.sections.hub import SECTION_HUB_META


def neighbors_by_pk(links, placement):
    links = list(links)
    pks = [link.pk for link in links]
    try:
        index = pks.index(placement.pk)
    except ValueError:
        return None, None
    return (
        links[index - 1] if index > 0 else None,
        links[index + 1] if index + 1 < len(links) else None,
    )


def task_group_page_nav_context(game, *, previous=None, following=None,
                                sections_project_id='sections', main_project_id='main'):
    if game.project_id == sections_project_id:
        back_label = 'К списку'
    elif game.project_id == main_project_id:
        back_label = 'К игре'
    else:
        back_label = 'Назад'
    section_meta = SECTION_HUB_META.get(game.id) or {}
    if section_meta.get('pager_label'):
        pager_label = section_meta['pager_label']
        pager_aria_label = section_meta.get('pager_aria_label') or (
            'Переход между заданиями «{}»'.format(pager_label)
        )
        results_label = section_meta.get('results_label') or 'Результаты'
    else:
        from games.daily.registry import get_daily_game

        daily_definition = get_daily_game(game.id)
        raw_label = (
            section_meta.get('title')
            or (daily_definition.title if daily_definition else None)
            or game.no_html_name
            or game.outside_name
            or game.name
            or 'Задание'
        )
        pager_label = strip_tags(str(raw_label)).strip() or 'Задание'
        pager_aria_label = 'Переход между заданиями «{}»'.format(pager_label)
        results_label = 'Результаты'
    return {
        'back_label': back_label,
        'task_group_pager_label': pager_label,
        'task_group_pager_aria_label': pager_aria_label,
        'task_group_results_label': results_label,
        'prev_task_group_number': previous.number if previous else None,
        'prev_task_group_name': previous.name if previous else None,
        'next_task_group_number': following.number if following else None,
        'next_task_group_name': following.name if following else None,
    }


def play_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/'.format(project_base, game.id, number)
    from games.sections.paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return section_play_path(game.id, number)
    return '/games/{}/{}/'.format(game.id, number)


def results_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/results/'.format(project_base, game.id, number)
    from games.sections.paths import is_root_section_game, section_play_path
    if is_root_section_game(game.id):
        return '{}results/'.format(section_play_path(game.id, number))
    return '/games/{}/{}/results/'.format(game.id, number)


def replay_url_for_task_group(game, number, *, project_base=''):
    if project_base:
        return '{}/games/{}/{}/replay/'.format(project_base, game.id, number)
    from games.sections.paths import section_replay_path
    return section_replay_path(game.id, number)


def replay_exit_url_for_task_group(game, number, *, project_base=''):
    return replay_url_for_task_group(game, number, project_base=project_base).rstrip('/') + '/exit/'
