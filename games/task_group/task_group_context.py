"""Assemble template context dicts for a task group (new UI task cards)."""

import games.puzzles.core.check_runtime  # noqa: F401 — before grid_puzzle collectors import
from games.site.pages.results.pages import _raddle_ui_state_for_actor
from games.task_group.daily_board_task_data import daily_board_task_data as collect_daily_board_data
from games.task_group.grid_puzzle_task_data import grid_puzzle_data_by_task_id as collect_grid_puzzle_data
from games.task_group.html_forms_task_data import html_forms_data_by_task_id as collect_html_forms_data
from games.task_group.likes_meta import likes_meta_by_task_id as collect_likes_meta
from games.task_group.proportions_task_data import proportions_chips_for_task_group as collect_proportions_chips
from games.task_group.replacements_lines_task_data import (
    replacements_lines_data_by_task_id as collect_replacements_lines_data,
)
from games.task_group.task_group_actor_context import bulk_attempts_and_gameplay_tokens
from games.task_group.task_group_placement import resolve_task_group_placement
from games.task_group.task_group_ui import task_ui_by_task_id_for_group as collect_task_ui_by_task_id
from games.task_group.wall_max_points import wall_max_points_meta_by_task_id as collect_wall_max_points_meta


def build_task_group_task_context_dicts(
    game,
    task_group,
    tasks,
    team,
    user,
    anon_key,
    mode,
    placement=None,
    replay_slot=None,
    request=None,
):
    """
    Shared context for task_group.html and new/partials/task_card.html
    (attempts, walls, replacements_lines, likes, proportions pool).
    """
    attempts_info_by_task_id, gameplay_context_tokens = bulk_attempts_and_gameplay_tokens(
        game,
        tasks,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )
    grid_puzzle_data = collect_grid_puzzle_data(tasks, attempts_info_by_task_id)
    wall_max_points_meta_by_task_id = collect_wall_max_points_meta(tasks)
    likes_meta_by_task_id = collect_likes_meta(tasks, team=team, user=user, anon_key=anon_key)
    placement = resolve_task_group_placement(game, task_group, placement)
    replacements_lines_data = collect_replacements_lines_data(
        tasks,
        attempts_info_by_task_id,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )
    html_forms_data = collect_html_forms_data(
        tasks,
        attempts_info_by_task_id,
        gameplay_context_tokens,
        request=request,
        game=game,
    )
    daily = collect_daily_board_data(
        tasks,
        attempts_info_by_task_id,
        game=game,
        task_group=task_group,
        placement=placement,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        raddle_ui_state_for_actor=_raddle_ui_state_for_actor,
    )
    board_data_by_context = daily['board_data_by_context']
    proportions_chips = collect_proportions_chips(task_group, tasks, attempts_info_by_task_id)
    task_ui_by_task_id = collect_task_ui_by_task_id(
        game,
        tasks,
        placement=placement,
        mode=mode,
        attempts_info_by_task_id=attempts_info_by_task_id,
        replacements_lines_data=replacements_lines_data,
        html_forms_data=html_forms_data,
        raddle_data=board_data_by_context.get('raddle', {}),
        word_salad_data=board_data_by_context.get('word_salad', {}),
        grid_puzzle_data=grid_puzzle_data,
        wall_max_points_meta_by_task_id=wall_max_points_meta_by_task_id,
        daily_board_context_key_by_task_id=daily['daily_board_context_key_by_task_id'],
        daily_board_template_by_task_id=daily['daily_board_template_by_task_id'],
        daily_board_wrapper_by_task_id=daily['daily_board_wrapper_by_task_id'],
        daily_board_adapter_by_task_id=daily['daily_board_adapter_by_task_id'],
    )
    return {
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'html_forms_data': html_forms_data,
        'grid_puzzle_data': grid_puzzle_data,
        'proportions_chips': proportions_chips,
        'task_ui_by_task_id': task_ui_by_task_id,
        'gameplay_context_tokens': gameplay_context_tokens,
        'daily_board_contexts': board_data_by_context,
        **{
            f'{context_key}_data': data
            for context_key, data in board_data_by_context.items()
        },
    }
