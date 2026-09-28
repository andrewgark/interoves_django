"""Small, request-independent helpers for results table contexts."""


def results_column_count(task_groups, mode='general'):
    """Return the actual number of columns in the shared results table."""
    fixed_columns = 4 if mode == 'tournament' else 3
    task_columns = sum(
        group.get_n_tasks_for_results() for group in (task_groups or [])
    )
    return fixed_columns + task_columns


def empty_results_rows_context():
    """Return the empty shape expected by results table templates."""
    return {
        'teams_sorted': [],
        'team_to_list_attempts_info': {},
        'team_to_cells': {},
        'team_to_score': {},
        'team_to_place': {},
        'team_to_max_best_time': {},
    }


def results_actor_filter_types(request):
    raw = request.GET.get('actors')
    if raw is None:
        return {'user', 'team', 'anon'}
    return {
        value.strip()
        for value in str(raw).split(',')
        if value.strip()
    } & {'user', 'team', 'anon'}


def results_actor_filter_urls(request):
    """Build links that toggle actor types while preserving other filters."""
    selected = results_actor_filter_types(request)
    definitions = (
        ('team', 'Команды', 'ph-users'),
        ('user', 'Игроки', 'ph-user'),
        ('anon', 'Анонимы', 'ph-detective'),
    )
    result = []
    for kind, label, icon in definitions:
        next_types = set(selected)
        if kind in next_types:
            next_types.remove(kind)
        else:
            next_types.add(kind)
        params = request.GET.copy()
        params.pop('page', None)
        params.pop('partial', None)
        params.pop('loaded', None)
        params['actors'] = ','.join(
            value for value in ('user', 'team', 'anon') if value in next_types
        )
        query = params.urlencode()
        result.append({
            'label': label,
            'icon': icon,
            'active': kind in selected,
            'url': request.path + ('?' + query if query else ''),
            'title': ('Скрыть ' if kind in selected else 'Показать ') + label.lower(),
        })
    return result
