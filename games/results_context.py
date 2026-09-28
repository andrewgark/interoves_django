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
