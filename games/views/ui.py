"""Main UI views (neutral naming, without 'new')."""

from games.views.new_ui import *  # noqa: F401,F403

# Neutral aliases for handlers.
hub = new_hub
folder = new_folder
section_game_page = new_section_game_page
section_results_page = new_section_results_page
main_game_page = new_main_game_page
results_page = new_results_page
tournament_results_page = new_tournament_results_page
task_group_page = new_task_group_page
get_answer = new_get_answer
get_replacements_line_answer = new_get_replacements_line_answer
like_dislike = new_like_dislike
set_play_mode = new_set_play_mode
migrate_anon_attempts = new_migrate_anon_attempts
profile = new_profile
team = new_team
pay_page = new_pay_page
create_ticket_payment = new_create_ticket_payment
team_name_check = new_team_name_check
team_info = new_team_info
team_create = new_team_create
team_request_join = new_team_request_join
team_join_by_password = new_team_join_by_password
team_password = new_team_password
team_rename = new_team_rename
