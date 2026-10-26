"""Main UI views (neutral naming, without ``new``)."""

from games.site.pages.hub.cards import get_section_games
from games.site.pages.hub.pages import (
    folder,
    hub,
    ladder_hub_page,
    ladder_last_page,
    ladder_today_page,
    section_last_page,
    start,
    project_folder_games,
    project_hub,
)
from games.site.pages.game.progress import (
    game_task_group_progress,
    project_game_task_group_progress,
)
from games.site.pages.game.pages import (
    main_game_page,
    section_game_page,
    project_main_game_page,
    project_results_page,
    project_task_group_page,
    project_tournament_results_page,
)
from games.site.pages.results.pages import (
    game_task_results_page,
    ladder_word_results_page,
    results_page,
    section_results_page,
    section_task_results_page,
    tournament_results_page,
)
from games.site.pages.play.page import daily_statistics, task_group_page
from games.site.pages.play.replay import replay_exit, replay_start
from games.site.pages.play.live import task_group_live_state
from games.site.pages.play.answers import (
    get_answer,
    get_raddle_word_answer,
    get_replacements_line_answer,
)
from games.site.pages.play.reactions import bug_report, like_dislike, raddle_clue_like
from games.site.pages.profile.anon import (
    anon_merge_job_status,
    anon_migrate_count,
    club_archive_offer_action,
    migrate_anon_attempts,
    set_play_mode,
)
from games.site.pages.profile.pages import (
    account_merge_confirm,
    account_merge_retry,
    account_merge_status,
    dismiss_unverified_telegram_handle,
    profile,
    profile_report_detail,
    profile_reports,
    social_account_disconnect,
)
from games.site.pages.team.pages import (
    team,
    team_create,
    team_info,
    team_invite,
    team_invite_accept,
    team_invite_create,
    team_join_by_password,
    team_join_page,
    team_name_check,
    team_password,
    team_rename,
    team_request_join,
    team_set_primary,
)
from games.site.pages.pay.pages import (
    create_crypto_donation,
    create_crypto_ticket_payment,
    create_ticket_payment,
    create_tribute_ticket_payment,
    donate_page,
    donation_status,
    pay_page,
    telegram_link_start,
    ticket_payment_status,
)

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
