"""Task-group template context facade (legacy import path + test hooks)."""
import logging

from games.tasks.presentation import task_ui_descriptor, wall_ui_context
from games.tasks.replacements_state import current_state as replacements_current_state
from games.tasks.task_group_context import build_task_group_task_context_dicts

logger = logging.getLogger('games.views.new_ui')


def _task_ui_descriptor(
    task, *, rld=None, html_forms=None, rd=None, wall_meta=None, ws=None, gp=None,
    board_context_key=None, body_template_override=None,
    body_wrapper_override=None, show_attempts_override=None,
    show_answer_override=None,
):
    """Compatibility wrapper for the task-card presentation contract."""
    return task_ui_descriptor(
        task,
        rld=rld,
        html_forms=html_forms,
        rd=rd,
        wall_meta=wall_meta,
        ws=ws,
        gp=gp,
        board_context_key=board_context_key,
        body_template_override=body_template_override,
        body_wrapper_override=body_wrapper_override,
        show_attempts_override=show_attempts_override,
        show_answer_override=show_answer_override,
    )


def _wall_ui_context(task, attempts_info, mode):
    """Compatibility wrapper for wall task presentation."""
    return wall_ui_context(task, attempts_info, mode)


def _replacements_current_state(
    task, *, game=None, team=None, user=None, anon_key=None, mode='general',
    replay_slot=None, attempts_info=None,
):
    """Compatibility wrapper for replacements state resolution."""
    return replacements_current_state(
        task,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts_info=attempts_info,
    )


__all__ = [
    '_task_ui_descriptor',
    '_wall_ui_context',
    '_replacements_current_state',
    'build_task_group_task_context_dicts',
]
