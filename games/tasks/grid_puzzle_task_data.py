"""Grid-puzzle template payloads keyed by task id (task-group UI)."""

from games.puzzles.replacements.grid_puzzle import GridPuzzleDataError, public_grid_puzzle_context


def grid_puzzle_data_by_task_id(tasks, attempts_info_by_task_id) -> dict:
    """Build ``{task_id: public_grid_puzzle_context(...)}`` for grid-puzzle tasks."""
    out = {}
    for t in tasks:
        if t.task_type != 'grid-puzzle':
            continue
        ai = attempts_info_by_task_id.get(t.id)
        solved = bool(ai and ai.is_solved())
        try:
            out[t.id] = public_grid_puzzle_context(
                t, reveal_solution=solved, readonly=solved,
            )
        except GridPuzzleDataError:
            continue
    return out
