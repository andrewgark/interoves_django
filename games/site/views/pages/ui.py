"""Main UI views (neutral naming, without ``new``)."""

from games.site.ui.game.pages import (
    new_main_game_page,
    new_section_game_page,
)
from games.site.ui.game.progress import (
    new_game_task_group_progress,
    project_game_task_group_progress,
)
from games.site.ui.hub.pages import (
    new_folder,
    new_hub,
    new_ladder_hub_page,
    new_ladder_last_page,
    new_ladder_today_page,
    new_section_last_page,
    new_start,
)
from games.site.ui.pay.pages import (
    new_create_crypto_donation,
    new_create_crypto_ticket_payment,
    new_create_ticket_payment,
    new_create_tribute_ticket_payment,
    new_donate_page,
    new_donation_status,
    new_pay_page,
    new_telegram_link_start,
    new_ticket_payment_status,
)
from games.site.ui.play.answers import (
    new_get_answer,
    new_get_raddle_word_answer,
    new_get_replacements_line_answer,
)
from games.site.ui.play.live import new_task_group_live_state
from games.site.ui.play.page import new_task_group_page
from games.site.ui.play.reactions import new_bug_report, new_like_dislike, new_raddle_clue_like
from games.site.ui.profile.anon import (
    new_anon_migrate_count,
    new_club_archive_offer_action,
    new_migrate_anon_attempts,
    new_set_play_mode,
)
from games.site.ui.profile.pages import (
    new_account_merge_confirm,
    new_account_merge_retry,
    new_account_merge_status,
    new_dismiss_unverified_telegram_handle,
    new_profile,
    new_profile_report_detail,
    new_profile_reports,
    new_social_account_disconnect,
)
from games.site.ui.results.pages import (
    new_game_task_results_page,
    new_ladder_word_results_page,
    new_results_page,
    new_section_results_page,
    new_section_task_results_page,
    new_tournament_results_page,
)
from games.site.ui.team.pages import (
    new_team,
    new_team_create,
    new_team_info,
    new_team_invite,
    new_team_invite_accept,
    new_team_invite_create,
    new_team_join_by_password,
    new_team_join_page,
    new_team_name_check,
    new_team_password,
    new_team_rename,
    new_team_request_join,
    new_team_set_primary,
)

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

from games.site.views.daily.censorly import (  # noqa: E402
    censorly_guess,
    censorly_hint,
    censorly_play_page,
    censorly_state,
)


def censorly_play_random(request, share_hash):
    return censorly_play_page(request, share_hash=share_hash)


def censorly_guess_random(request, share_hash):
    return censorly_guess(request, share_hash=share_hash)


def censorly_state_random(request, share_hash):
    return censorly_state(request, share_hash=share_hash)


def censorly_hint_random(request, share_hash):
    return censorly_hint(request, share_hash=share_hash)


game_task_group_progress = new_game_task_group_progress
project_game_task_group_progress = project_game_task_group_progress
get_answer = new_get_answer
get_replacements_line_answer = new_get_replacements_line_answer
get_raddle_word_answer = new_get_raddle_word_answer
like_dislike = new_like_dislike
raddle_clue_like = new_raddle_clue_like
bug_report = new_bug_report
set_play_mode = new_set_play_mode
migrate_anon_attempts = new_migrate_anon_attempts
anon_migrate_count = new_anon_migrate_count
club_archive_offer_action = new_club_archive_offer_action
profile = new_profile
dismiss_unverified_telegram_handle = new_dismiss_unverified_telegram_handle
profile_reports = new_profile_reports
profile_report_detail = new_profile_report_detail
account_merge_confirm = new_account_merge_confirm
account_merge_status = new_account_merge_status
account_merge_retry = new_account_merge_retry
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
team_invite = new_team_invite
team_invite_accept = new_team_invite_accept
team_invite_create = new_team_invite_create
