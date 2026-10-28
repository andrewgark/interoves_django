"""Retired old interface: /old/… links redirect to the new UI or answer 410."""

from django.urls import path
from django.urls import re_path as url

from games.site.views.account.ticket import check_order
from games.site.views.pages import legacy

_OLD_PROJECT_HOMES = ('main', 'glowbyte', 'tinkoff', 'lksh', 'tennet', 'tinkoffgeneration', 'umind')

urlpatterns = [
    path("", legacy.project_home),
    *[path(f"{project_id}/", legacy.project_home, {"project_id": project_id}) for project_id in _OLD_PROJECT_HOMES],
    path("create_team/", legacy.to_path("/team/")),
    path("join_team/", legacy.to_path("/team/")),
    path("quit_from_team/", legacy.to_path("/team/")),
    path("request_ticket/", legacy.to_path("/pay/")),
    url(r"^confirm_user_joining_team/(?P<user_id>\d+)/$", legacy.to_path("/confirm_user_joining_team/{user_id}/")),
    url(r"^reject_user_joining_team/(?P<user_id>\d+)/$", legacy.to_path("/reject_user_joining_team/{user_id}/")),
    url(r"^kick_out_user/(?P<user_id>\d+)/$", legacy.to_path("/kick_out_user/{user_id}/")),
    url(r"^register/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy.to_path("/register/{game_id}/")),
    url(r"^games/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy.game),
    url(r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)$", legacy.game),
    url(r"^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)/(?P<task>[0-9.a-zA-Zа-яА-Я]+)$", legacy.game),
    url(r"^results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy.results),
    url(r"^tournament_results/(?P<game_id>[a-zA-Z0-9_]+)/$", legacy.tournament_results),
    url(r"^cw_hint/?$", legacy.to_path("/cw_hint")),
    url(r"^easter_egg_2021/?$", legacy.to_path("/easter_egg_2021/")),
    url(r"^meta100-1/?$", legacy.to_path("/meta100-1")),
    url(r"^meta100-2/?$", legacy.to_path("/meta100-2")),
    # Retired payment webhook: answers 410 and never processes the payload.
    url(r"^check-order/", check_order, name="check_order"),
    # Old AJAX endpoints, total results and anything else.
    url(r"", legacy.gone),
]
