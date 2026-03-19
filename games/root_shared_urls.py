"""
Gameplay and team-action URLs at site root.

The main UI templates POST to /send_attempt/, /send_hint_attempt/, link to
/games/..., /register/..., /kick_out_user/..., etc. Those routes used to live
at the root urlpatterns; after moving the legacy site under /old/, they must
still be registered here (without duplicating URL *names* from games.old_urls).
"""

from django.urls import re_path as url

from games.views.registration import register_to_game
from games.views.views import (
    confirm_user_joining_team,
    game_page,
    get_answer,
    kick_out_user,
    like_dislike,
    reject_user_joining_team,
    send_attempt,
    send_hint_attempt,
)

urlpatterns = [
    url(r"^send_attempt/(?P<task_id>\d+)/$", send_attempt),
    url(r"^send_hint_attempt/(?P<task_id>\d+)/$", send_hint_attempt),
    url(r"^get_answer/(?P<task_id>\d+)/$", get_answer),
    url(r"^like_dislike/(?P<task_id>\d+)/", like_dislike),
    url(r"^register/(?P<game_id>[a-zA-Z0-9_]+)/$", register_to_game),
    # Do not register /games/<id>/ here — that path is the main UI hub (games/ui_urls.py).
    # Only legacy deep links (task group / task) for "open in old interface".
    url(r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)$", game_page),
    url(
        r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)/(?P<task>[0-9.a-zA-Zа-яА-Я]+)$",
        game_page,
    ),
    url(r"^confirm_user_joining_team/(?P<user_id>\d+)/$", confirm_user_joining_team),
    url(r"^reject_user_joining_team/(?P<user_id>\d+)/$", reject_user_joining_team),
    url(r"^kick_out_user/(?P<user_id>\d+)/$", kick_out_user),
]
