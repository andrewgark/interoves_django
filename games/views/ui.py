"""Main UI views (neutral naming, without 'new')."""

from games.views.new_ui import *  # noqa: F401,F403

# Neutral aliases for handlers.
hub = new_hub
folder = new_folder
section_game_page = new_section_game_page
section_results_page = new_section_results_page
ladder_word_results_page = new_ladder_word_results_page
main_game_page = new_main_game_page
results_page = new_results_page
tournament_results_page = new_tournament_results_page
task_group_page = new_task_group_page
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
team = new_team
pay_page = new_pay_page
create_ticket_payment = new_create_ticket_payment
create_crypto_ticket_payment = new_create_crypto_ticket_payment
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
