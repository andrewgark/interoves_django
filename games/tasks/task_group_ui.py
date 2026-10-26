"""Per-task UI descriptors for task-group pages."""

from games.tasks.presentation import task_ui_descriptor, wall_ui_context
from games.tasks.titles import task_display_name


def task_ui_by_task_id_for_group(
    game,
    tasks,
    *,
    placement,
    mode,
    attempts_info_by_task_id,
    replacements_lines_data,
    html_forms_data,
    raddle_data,
    word_salad_data,
    grid_puzzle_data,
    wall_max_points_meta_by_task_id,
    daily_board_context_key_by_task_id,
    daily_board_template_by_task_id,
    daily_board_wrapper_by_task_id,
    daily_board_adapter_by_task_id,
) -> dict:
    """Build ``task_ui_by_task_id`` for templates and AJAX partials."""
    out = {}
    for t in tasks:
        adapter = daily_board_adapter_by_task_id.get(t.id)
        out[t.id] = {
            **task_ui_descriptor(
                t,
                rld=replacements_lines_data.get(t.id),
                html_forms=html_forms_data.get(t.id),
                rd=raddle_data.get(t.id),
                wall_meta=wall_max_points_meta_by_task_id.get(t.id),
                ws=word_salad_data.get(t.id),
                gp=grid_puzzle_data.get(t.id),
                board_context_key=daily_board_context_key_by_task_id.get(t.id),
                body_template_override=daily_board_template_by_task_id.get(t.id),
                body_wrapper_override=daily_board_wrapper_by_task_id.get(t.id),
                show_attempts_override=adapter.show_attempts if adapter is not None else None,
                show_answer_override=adapter.show_answer if adapter is not None else None,
            ),
            'display_name': task_display_name(game, t, placement=placement),
            'wall': wall_ui_context(t, attempts_info_by_task_id.get(t.id), mode),
        }
    return out
