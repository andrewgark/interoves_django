"""Раздача по командам: the attempt list shows that team's line. The view still saves the attempt."""

from __future__ import annotations

import json

from games.puzzles.base import Puzzle


def pretty_text(attempt):
    """The posted answer plus the line assigned to this team."""
    try:
        from games.models import GameTaskGroup
        game = attempt.game_id and attempt.game or GameTaskGroup.resolve_game_for_task(attempt.task)
        distr_text = json.loads(attempt.task.text)['list'][attempt.team.get_team_reg_number(game)]
    except Exception as exc:
        distr_text = 'ERROR: {}'.format(exc)
    return '{} ({})'.format(attempt.text, distr_text)


class DistributeToTeamsPuzzle(Puzzle):
    task_type = 'distribute_to_teams'

    pretty_text = staticmethod(pretty_text)
