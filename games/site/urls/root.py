"""
Gameplay and team-action URLs at site root.

The main UI templates POST to /send_attempt/, /send_hint_attempt/,
/register/..., /kick_out_user/..., etc. Task texts link to puzzle pages
(/cw_hint, /meta100-1, …).
"""

from django.urls import path
from django.urls import re_path as url

from games.site.pages import legacy_results_redirect, legacy_tournament_results_redirect
from games.site.views.account.registration import register_to_game
from games.site.views.account.team import (
    confirm_user_joining_team,
    kick_out_user,
    quit_from_team,
    reject_user_joining_team,
)
from games.site.views.game.attempt import send_attempt
from games.site.views.game.hint import send_hint_attempt
from games.site.views.game.raddle import send_raddle_assist, send_raddle_ui
from games.site.views.pages import legacy
from games.site.views.pages.meta import capitals_task_100, countries_task_100
from games.site.views.pages.other import easter_egg_2021, return_intentional_503

urlpatterns = [
    url(r"^send_attempt/(?P<task_id>\d+)/$", send_attempt),
    url(r"^send_hint_attempt/(?P<task_id>\d+)/$", send_hint_attempt),
    url(r"^send_raddle_assist/(?P<task_id>\d+)/$", send_raddle_assist),
    url(r"^send_raddle_ui/(?P<task_id>\d+)/$", send_raddle_ui),
    url(r"^register/(?P<game_id>[a-zA-Z0-9_]+)/$", register_to_game),
    url(r"^results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy_results_redirect),
    url(r"^tournament_results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy_tournament_results_redirect),
    # Do not register /games/<id>/ or /games/<id>/<n> here — those are the main UI (games.site.urls.ui).
    # Old-interface deep link to a single task (three path segments, no trailing slash) → its task group.
    url(
        r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)/(?P<task>[0-9.a-zA-Zа-яА-Я]+)$",
        legacy.game,
    ),
    url(r"^confirm_user_joining_team/(?P<user_id>\d+)/$", confirm_user_joining_team),
    url(r"^reject_user_joining_team/(?P<user_id>\d+)/$", reject_user_joining_team),
    url(r"^kick_out_user/(?P<user_id>\d+)/$", kick_out_user),
    path("quit_from_team/", quit_from_team, name="quit_from_team"),
    # Puzzle pages that task texts link to (/cw_hint, /meta100-1?key=…).
    url(r"^cw_hint/?$", return_intentional_503),
    url(r"^easter_egg_2021/?$", easter_egg_2021),
    url(r"^meta100-1/?$", capitals_task_100),
    url(r"^meta100-2/?$", countries_task_100),
]
