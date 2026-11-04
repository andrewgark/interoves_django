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
from games.task_group.text_with_forms_task_data import (
    hidden_until_solved_by_task_id as collect_hidden_until_solved,
)
from games.task_group.text_with_forms_task_data import (
    support_tasks as collect_support_tasks,
)
from games.task_group.text_with_forms_task_data import (
    task_group_heading_html,
)
from games.task_group.text_with_forms_task_data import (
    text_with_forms_data_by_task_id as collect_text_with_forms_data,
)
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
    # text_with_forms cards embed other tasks' answer fields; hidden cards
    # depend on other tasks being solved — load those tasks' attempts too.
    attempts_info_by_task_id, gameplay_context_tokens = bulk_attempts_and_gameplay_tokens(
        game,
        list(tasks) + collect_support_tasks(tasks),
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
    text_with_forms_data = collect_text_with_forms_data(tasks, attempts_info_by_task_id)
    hidden_until_solved = collect_hidden_until_solved(tasks, attempts_info_by_task_id)
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
    client_checks = collect_client_checks(
        game, tasks, attempts_info_by_task_id, team=team, user=user, anon_key=anon_key, mode=mode,
    )
    return {
        'client_specs_by_task_id': {task_id: check['spec_json'] for task_id, check in client_checks.items()},
        'offline_answer_task_ids': {task_id for task_id, check in client_checks.items() if check['offline_attempts']},
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'html_forms_data': html_forms_data,
        'text_with_forms_data': text_with_forms_data,
        'hidden_until_solved': hidden_until_solved,
        'tg_heading_html': task_group_heading_html(placement, attempts_info_by_task_id),
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


# Task types answered through the ordinary text form (task-default.html).
ORDINARY_ANSWER_TASK_TYPES = ('default',)


def collect_client_checks(game, tasks, attempts_info_by_task_id, *, team, user, anon_key, mode):
    """``page_client_check`` of each unsolved ordinary task the page may check itself.

    docs/offline-play-plan.md, phase 3: the answer form carries the spec and
    shows the verdict before the server's answer arrives; with offline
    attempts it also keeps answers given without the network.
    """
    from games.gameplay.client_checks import page_client_check

    specs = {}
    for task in tasks:
        if task.task_type not in ORDINARY_ANSWER_TASK_TYPES:
            continue
        info = attempts_info_by_task_id.get(task.id)
        if info is not None and info.is_solved():
            continue
        check = page_client_check(task=task, game=game, mode=mode, team=team, user=user, anon_key=anon_key)
        if check is not None:
            specs[task.id] = check
    return specs
