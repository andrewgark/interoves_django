"""Canonical public URL paths for section games (ladder, alphabetty, …)."""

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID

# Served at site root: /ladder/, /alphabetty/ (not /games/<id>/).
ROOT_SECTION_GAME_IDS = frozenset({LADDER_GAME_ID, ALPHABETTY_GAME_ID})


def is_root_section_game(game_id) -> bool:
    return game_id in ROOT_SECTION_GAME_IDS


def section_hub_path(game_id: str) -> str:
    """Hub URL for a sections-project game."""
    if game_id in ROOT_SECTION_GAME_IDS:
        return '/{}/'.format(game_id)
    return '/section/{}/'.format(game_id)


def section_play_path(game_id: str, number) -> str:
    """Play URL for one published number/round."""
    if game_id in ROOT_SECTION_GAME_IDS:
        return '/{}/{}/'.format(game_id, number)
    return '/games/{}/{}/'.format(game_id, number)


def section_progress_path(game_id: str) -> str:
    if game_id in ROOT_SECTION_GAME_IDS:
        return '/{}/progress/'.format(game_id)
    return '/games/{}/progress/'.format(game_id)


def ladder_word_results_path(number) -> str:
    return '/{}/{}/results/'.format(LADDER_GAME_ID, number)
