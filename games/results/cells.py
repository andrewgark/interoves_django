"""Build per-task cell metadata for results table rows."""

from games.results.context import result_cell_class


def build_results_cells(*, participants, tasks, attempts_by_participant, alphabetty=False):
    """Return display metadata for each participant's task cells."""
    def to_float(value):
        try:
            return float(value)
        except Exception:
            return 0.0

    # Compute once per task; some task types parse a regex on every call.
    task_max_points = {}
    for task in tasks:
        try:
            maximum = (
                task.get_results_max_points()
                if hasattr(task, 'get_results_max_points')
                else getattr(task, 'get_points', None)()
                if hasattr(task, 'get_points')
                else getattr(task, 'points', 0)
            )
            task_max_points[task.id] = to_float(maximum)
        except Exception:
            task_max_points[task.id] = 0.0

    result = {}
    for participant in participants:
        cells = []
        attempts_list = attempts_by_participant.get(participant, [])
        for index, task in enumerate(tasks):
            info = attempts_list[index] if index < len(attempts_list) else None
            maximum = task_max_points[task.id]
            points = 0.0
            has_attempts = False
            attempt_count = 0
            hint_numbers = []
            if info:
                try:
                    attempt_count = int(
                        info.get_n_attempts()
                        if callable(getattr(info, 'get_n_attempts', None))
                        else (info.get_n_attempts or 0)
                    )
                    has_attempts = attempt_count > 0
                except Exception:
                    has_attempts = False
                    attempt_count = 0
                try:
                    points = to_float(info.get_result_points())
                except Exception:
                    points = 0.0
                try:
                    hint_numbers = (
                        info.get_hint_numbers()
                        if callable(getattr(info, 'get_hint_numbers', None))
                        else []
                    )
                except Exception:
                    hint_numbers = []

            cells.append({
                'cls': result_cell_class(points, maximum, has_attempts=has_attempts),
                'n_attempts': attempt_count,
                'result_points': points,
                'hint_numbers': hint_numbers,
                'solved': (
                    alphabetty and maximum > 0 and points >= maximum - 1e-9
                ),
                'answer': getattr(task, 'answer', '') if alphabetty else '',
            })
        result[participant] = cells
    return result
