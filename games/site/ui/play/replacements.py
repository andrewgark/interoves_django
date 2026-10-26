"""Main UI: hub, games folder, profile, team."""
import logging


from games.tasks.replacements_state import line_done_list as replacements_line_done_list


logger = logging.getLogger('games.views.new_ui')

def _replacements_lines_line_done_list(
    task, attempts_info, *, game=None, team=None, user=None, anon_key=None,
    mode='general', replay_slot=None,
):
    """Compatibility wrapper for replacements line completion."""
    return replacements_line_done_list(
        task,
        attempts_info,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
    )


__all__ = [
    '_replacements_lines_line_done_list',
]
