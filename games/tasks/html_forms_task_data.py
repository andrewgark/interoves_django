"""HTML-forms task payloads for task-group UI."""

from games.puzzles.replacements.forms import render_html_forms_task


def html_forms_data_by_task_id(
    tasks,
    attempts_info_by_task_id,
    gameplay_context_tokens,
    *,
    request,
    game,
) -> dict:
    """Build per-task html-forms render context keyed by task id."""
    out = {}
    for t in tasks:
        if t.task_type != 'html_forms':
            continue
        ai = attempts_info_by_task_id.get(t.id)
        out[t.id] = {
            'html': render_html_forms_task(
                request,
                t,
                ai,
                gameplay_context_tokens.get(t.id, ''),
                game=game,
                new_ui=True,
            ),
            'max_points_total': t.get_results_max_points(),
        }
    return out
