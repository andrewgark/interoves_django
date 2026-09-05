"""Main UI views (neutral naming, without 'new')."""

from games.views.new_ui import *  # noqa: F401,F403

# Neutral aliases for handlers.
hub = new_hub
start = new_start
folder = new_folder
section_game_page = new_section_game_page
section_results_page = new_section_results_page
ladder_word_results_page = new_ladder_word_results_page
section_task_results_page = new_section_task_results_page
game_task_results_page = new_game_task_results_page
main_game_page = new_main_game_page
results_page = new_results_page
tournament_results_page = new_tournament_results_page
task_group_page = new_task_group_page
task_group_live_state = new_task_group_live_state
ladder_today_page = new_ladder_today_page
ladder_last_page = new_ladder_last_page
ladder_hub_page = new_ladder_hub_page
section_last_page = new_section_last_page

from games.views.alphabetty_views import (  # noqa: E402
    alphabetty_guess,
    alphabetty_hint,
    alphabetty_hub_page,
    alphabetty_last_page,
    alphabetty_play_page,
    alphabetty_prefix,
    alphabetty_state,
    alphabetty_suggest,
    alphabetty_today_page,
)
from games.views.offer_alphabetty import (  # noqa: E402
    offer_alphabetty_create as alphabetty_create_submit,
    offer_alphabetty_detail,
    offer_alphabetty_page as alphabetty_create_page,
    offer_alphabetty_reopen,
    offer_alphabetty_send,
)
from games.views.offer_ladder import (  # noqa: E402
    offer_ladder_create,
    offer_ladder_detail,
    offer_ladder_page,
    offer_ladder_reset,
    offer_ladder_send,
)
from games.views.offer_salad import (  # noqa: E402
    offer_salad_create,
    offer_salad_detail,
    offer_salad_page,
    offer_salad_reset,
    offer_salad_send,
)
from games.views.daily_timing_views import daily_solve_timing  # noqa: E402

game_task_group_progress = new_game_task_group_progress
project_game_task_group_progress = project_game_task_group_progress
get_answer = new_get_answer
get_replacements_line_answer = new_get_replacements_line_answer
get_raddle_word_answer = new_get_raddle_word_answer
like_dislike = new_like_dislike
bug_report = new_bug_report
set_play_mode = new_set_play_mode
migrate_anon_attempts = new_migrate_anon_attempts
anon_migrate_count = new_anon_migrate_count
profile = new_profile
profile_reports = new_profile_reports
profile_report_detail = new_profile_report_detail
account_merge_confirm = new_account_merge_confirm
social_account_disconnect = new_social_account_disconnect
team = new_team
pay_page = new_pay_page
create_ticket_payment = new_create_ticket_payment
create_crypto_ticket_payment = new_create_crypto_ticket_payment
create_tribute_ticket_payment = new_create_tribute_ticket_payment
telegram_link_start = new_telegram_link_start
ticket_payment_status = new_ticket_payment_status
donate_page = new_donate_page
create_crypto_donation = new_create_crypto_donation
donation_status = new_donation_status
team_name_check = new_team_name_check
team_info = new_team_info
team_create = new_team_create
team_join_page = new_team_join_page
team_request_join = new_team_request_join
team_join_by_password = new_team_join_by_password
team_password = new_team_password
team_rename = new_team_rename
team_set_primary = new_team_set_primary
