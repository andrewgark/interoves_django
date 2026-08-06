"""Canonical public URL paths for section games (ladder, alphabetty, …)."""

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID

# Served at site root: /ladder/, /walls/, … (not /section/<id>/ or /games/<id>/ hub).
ROOT_SECTION_GAME_IDS = frozenset({
    LADDER_GAME_ID,
    ALPHABETTY_GAME_ID,
    'replacements',
    'walls',
    'palindromes',
    'week_task',
})

# Hub + task_group at /{id}/… (ladder/alphabetty have extra routes: today, guess, …).
STANDARD_ROOT_SECTION_GAME_IDS = tuple(
    sorted(g for g in ROOT_SECTION_GAME_IDS if g not in (LADDER_GAME_ID, ALPHABETTY_GAME_ID))
)


def is_root_section_game(game_id) -> bool:
    return game_id in ROOT_SECTION_GAME_IDS


def section_hub_path(game_id: str) -> str:
    """Hub URL for a sections-project game."""
    if is_root_section_game(game_id):
        return '/{}/'.format(game_id)
    return '/section/{}/'.format(game_id)


def section_play_path(game_id: str, number) -> str:
    """Play URL for one published number/round."""
    if is_root_section_game(game_id):
        return '/{}/{}/'.format(game_id, number)
    return '/games/{}/{}/'.format(game_id, number)


def section_last_path(game_id: str) -> str:
    """Stable CTA URL: redirects to the latest published round at request time."""
    if is_root_section_game(game_id):
        return '/{}/last/'.format(game_id)
    return '/games/{}/last/'.format(game_id)


def section_progress_path(game_id: str) -> str:
    if is_root_section_game(game_id):
        return '/{}/progress/'.format(game_id)
    return '/games/{}/progress/'.format(game_id)


def section_results_path(game_id: str) -> str:
    if game_id == LADDER_GAME_ID:
        return '/ladder/results/'
    if is_root_section_game(game_id):
        return '/{}/results/'.format(game_id)
    return '/section/{}/results/'.format(game_id)


def ladder_word_results_path(number) -> str:
    return '/{}/{}/results/'.format(LADDER_GAME_ID, number)
