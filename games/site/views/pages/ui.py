"""Main UI views (neutral naming, without ``new``)."""

from games.site.pages.hub.cards import get_section_games
from games.site.pages.hub.pages import (
    new_folder,
    new_hub,
    new_ladder_hub_page,
    new_ladder_last_page,
    new_ladder_today_page,
    new_section_last_page,
    new_start,
    project_folder_games,
    project_hub,
)
from games.site.pages.game.progress import (
    new_game_task_group_progress,
    project_game_task_group_progress,
)
from games.site.pages.game.pages import (
    new_main_game_page,
    new_section_game_page,
    project_main_game_page,
    project_results_page,
    project_task_group_page,
    project_tournament_results_page,
)
from games.site.pages.results.pages import (
    new_game_task_results_page,
    new_ladder_word_results_page,
    new_results_page,
    new_section_results_page,
    new_section_task_results_page,
    new_tournament_results_page,
)
from games.site.pages.play.page import daily_statistics, new_task_group_page
from games.site.pages.play.replay import new_replay_exit, new_replay_start
from games.site.pages.play.live import new_task_group_live_state
from games.site.pages.play.answers import (
    new_get_answer,
    new_get_raddle_word_answer,
    new_get_replacements_line_answer,
)
from games.site.pages.play.reactions import new_bug_report, new_like_dislike, new_raddle_clue_like
from games.site.pages.profile.anon import (
    new_anon_merge_job_status,
    new_anon_migrate_count,
    new_club_archive_offer_action,
    new_migrate_anon_attempts,
    new_set_play_mode,
)
from games.site.pages.profile.pages import (
    new_account_merge_confirm,
    new_account_merge_retry,
    new_account_merge_status,
    new_dismiss_unverified_telegram_handle,
    new_profile,
    new_profile_report_detail,
    new_profile_reports,
    new_social_account_disconnect,
)
from games.site.pages.team.pages import (
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
from games.site.pages.pay.pages import (
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
replay_start = new_replay_start
replay_exit = new_replay_exit
anon_merge_job_status = new_anon_merge_job_status
ladder_today_page = new_ladder_today_page
ladder_last_page = new_ladder_last_page
ladder_hub_page = new_ladder_hub_page
section_last_page = new_section_last_page

from games.site.views.daily.alphabetty import (  # noqa: E402
    alphabetty_guess,
    alphabetty_hint,
    alphabetty_hub_page,
    alphabetty_random_game,
    alphabetty_last_page,
    alphabetty_play_page,
    alphabetty_prefix,
    alphabetty_state,
    alphabetty_suggest,
    alphabetty_today_page,
)
from games.site.views.daily.censorly import (  # noqa: E402
    censorly_guess,
    censorly_hint,
    censorly_hub_page,
    censorly_last_page,
    censorly_play_page,
    censorly_random_game,
    censorly_state,
    censorly_today_page,
    censorly_word_report,
)


def censorly_play_random(request, share_hash):
    return censorly_play_page(request, share_hash=share_hash)


def censorly_guess_random(request, share_hash):
    return censorly_guess(request, share_hash=share_hash)


def censorly_state_random(request, share_hash):
    return censorly_state(request, share_hash=share_hash)


def censorly_hint_random(request, share_hash):
    return censorly_hint(request, share_hash=share_hash)

from games.site.views.offers.alphabetty import (  # noqa: E402
    offer_alphabetty_create as alphabetty_create_submit,
    offer_alphabetty_detail,
    offer_alphabetty_page as alphabetty_create_page,
    offer_alphabetty_reopen,
    offer_alphabetty_send,
)
from games.site.views.offers.ladder import (  # noqa: E402
    offer_ladder_create,
    offer_ladder_detail,
    offer_ladder_page,
    offer_ladder_reset,
    offer_ladder_send,
)
from games.site.views.offers.salad import (  # noqa: E402
    offer_salad_create,
    offer_salad_convert,
    offer_salad_detail,
    offer_salad_page,
    offer_salad_reset,
    offer_salad_send,
)
from games.site.views.offers.censorly import (  # noqa: E402
    offer_censorly_create,
    offer_censorly_page,
    offer_censorly_suggest,
)
from games.site.views.daily.timing import daily_solve_timing  # noqa: E402
from games.daily.ladder.tutorial.views import ladder_tutorial  # noqa: E402

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

from games.site.views.account.subscription import (  # noqa: E402
    subscription_checkout,
    subscription_page,
    subscription_payment_method_detach,
    subscription_yookassa_cancel,
    subscription_yookassa_monthly_start,
    subscription_yookassa_resume,
    subscription_claim_gift,
    subscription_reveal_gift_code,
    subscription_gift_tribute_start,
    subscription_gift_yookassa_start,
)
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
