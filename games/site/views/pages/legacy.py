from games.views.main_page import MainPageView
from games.views.total_results import total_results_page
from games.views.team_views import (
    create_team, join_team, quit_from_team, process_user_request,
    confirm_user_joining_team, reject_user_joining_team, kick_out_user,
    get_team_to_play_page,
)
from games.views.game_views import game_page, get_tournament_results
from games.views.attempt_views import check_attempt, get_first_new_hint, process_send_attempt, send_attempt
from games.views.hint_views import create_hint_attempt, process_send_hint_attempt, send_hint_attempt
from games.views.raddle_views import (
    process_send_raddle_assist,
    process_send_raddle_ui,
    send_raddle_assist,
    send_raddle_ui,
)
from games.views.answer_views import task_ok_by_team, get_answer
from games.views.results_views import results_page
from games.views.other_views import like_dislike, return_intentional_503, easter_egg_2021

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
    'process_send_raddle_assist',
    'process_send_raddle_ui',
    'send_raddle_assist',
    'send_raddle_ui',
    'task_ok_by_team',
    'get_answer',
    'results_page',
    'like_dislike',
    'return_intentional_503',
    'easter_egg_2021',
] 