# Import all views from the split modules to maintain backward compatibility

# Project views
from .main_page import MainPageView
from .total_results import total_results_page

# Team views
from .team_views import (
    create_team, join_team, quit_from_team, process_user_request,
    confirm_user_joining_team, reject_user_joining_team, kick_out_user,
    get_team_to_play_page
)

# Game views
from .game_views import game_page, get_tournament_results

# Attempt views
from .attempt_views import check_attempt, get_first_new_hint, process_send_attempt, send_attempt

# Hint views
from .hint_views import create_hint_attempt, process_send_hint_attempt, send_hint_attempt

# Answer views
from .answer_views import task_ok_by_team, get_answer

# Results views
from .results_views import results_page

# Other views
from .other_views import like_dislike, return_intentional_503, easter_egg_2021

# Export all functions for backward compatibility
__all__ = [
    'MainPageView',
    'total_results_page',
    'create_team',
    'join_team',
    'quit_from_team',
    'process_user_request',
    'confirm_user_joining_team',
    'reject_user_joining_team',
    'kick_out_user',
    'get_team_to_play_page',
    'game_page',
    'get_tournament_results',
    'check_attempt',
    'get_first_new_hint',
    'process_send_attempt',
    'send_attempt',
    'create_hint_attempt',
    'process_send_hint_attempt',
    'send_hint_attempt',
    'task_ok_by_team',
    'get_answer',
    'results_page',
    'like_dislike',
    'return_intentional_503',
    'easter_egg_2021',
] 