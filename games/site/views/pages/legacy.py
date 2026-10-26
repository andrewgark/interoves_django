from games.site.views.account.team import (
    confirm_user_joining_team,
    create_team,
    get_team_to_play_page,
    join_team,
    kick_out_user,
    process_user_request,
    quit_from_team,
    reject_user_joining_team,
)
from games.site.views.game.answer import get_answer, task_ok_by_team
from games.site.views.game.attempt import (
    check_attempt,
    get_first_new_hint,
    process_send_attempt,
    send_attempt,
)
from games.site.views.game.hint import (
    create_hint_attempt,
    process_send_hint_attempt,
    send_hint_attempt,
)
from games.site.views.game.page import game_page, get_tournament_results
from games.site.views.game.raddle import (
    process_send_raddle_assist,
    process_send_raddle_ui,
    send_raddle_assist,
    send_raddle_ui,
)
from games.site.views.pages.other import easter_egg_2021, like_dislike, return_intentional_503
from games.site.views.pages.public.main import MainPageView
from games.site.views.results.pages import results_page
from games.site.views.results.total import total_results_page

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
