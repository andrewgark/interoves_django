"""
Gameplay and team-action URLs at site root.

The main UI templates POST to /send_attempt/, /send_hint_attempt/, link to
/games/..., /register/..., /kick_out_user/..., etc. Those routes used to live
at the root urlpatterns; after moving the legacy site under /old/, they must
still be registered here (without duplicating URL *names* from games.site.urls.old).
"""

from django.urls import re_path as url

from games.site.pages import legacy_results_redirect, legacy_tournament_results_redirect
from games.site.views.account.registration import register_to_game
from games.site.views.account.team import (
    confirm_user_joining_team,
    kick_out_user,
    reject_user_joining_team,
)
from games.site.views.game.answer import get_answer
from games.site.views.game.attempt import send_attempt
from games.site.views.game.hint import send_hint_attempt
from games.site.views.game.page import game_page
from games.site.views.game.raddle import send_raddle_assist, send_raddle_ui
from games.site.views.pages.other import like_dislike

urlpatterns = [
    url(r"^send_attempt/(?P<task_id>\d+)/$", send_attempt),
    url(r"^send_hint_attempt/(?P<task_id>\d+)/$", send_hint_attempt),
    url(r"^send_raddle_assist/(?P<task_id>\d+)/$", send_raddle_assist),
    url(r"^send_raddle_ui/(?P<task_id>\d+)/$", send_raddle_ui),
    url(r"^get_answer/(?P<task_id>\d+)/$", get_answer),
    url(r"^like_dislike/(?P<task_id>\d+)/", like_dislike),
    url(r"^register/(?P<game_id>[a-zA-Z0-9_]+)/$", register_to_game),
    url(r"^results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy_results_redirect),
    url(r"^tournament_results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy_tournament_results_redirect),
    # Do not register /games/<id>/ or /games/<id>/<n> here — those are the main UI (games.site.urls.ui).
    # Only legacy deep link to a single task in the old interface (three path segments, no trailing slash).
    url(
        r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)/(?P<task>[0-9.a-zA-Zа-яА-Я]+)$",
        game_page,
    ),
    url(r"^confirm_user_joining_team/(?P<user_id>\d+)/$", confirm_user_joining_team),
    url(r"^reject_user_joining_team/(?P<user_id>\d+)/$", reject_user_joining_team),
    url(r"^kick_out_user/(?P<user_id>\d+)/$", kick_out_user),
]
