from django.urls import path, re_path
from django.views.generic import RedirectView

from games.views import ui
from games.views.registration import register_to_game
from games.views.views import (
    confirm_user_joining_team,
    kick_out_user,
    reject_user_joining_team,
)


# Project-scoped UI prefixes like /glowbyte/..., must not swallow built-in roots like /games/ or /section/.
_PROJECT_ID_RE = r'(?P<project_id>(?!admin|accounts|old|games|section|team|profile|pay|answer|like-dislike|play-mode|migrate-anon-attempts|anon-migrate-count|health|meta|inline-edit|explorer|yookassa|privacy-policy|terms-of-use|tickets|ticket-agreement|vpn|logout|nutrimatic-ru|eurovision_booklet)[a-zA-Z0-9_-]+)'

urlpatterns = [
    # Project-scoped "new UI" pages (isolated navigation per project).
    re_path(r'^' + _PROJECT_ID_RE + r'/$', ui.project_hub, name='project_hub'),
    re_path(r'^' + _PROJECT_ID_RE + r'/games/$', ui.project_folder_games, name='project_folder_games'),
    re_path(r'^' + _PROJECT_ID_RE + r'/games/(?P<game_id>[a-zA-Z0-9_]+)/$', ui.project_main_game_page, name='project_main_game'),
    re_path(r'^' + _PROJECT_ID_RE + r'/games/(?P<game_id>[a-zA-Z0-9_]+)/results/$', ui.project_results_page, name='project_results'),
    re_path(r'^' + _PROJECT_ID_RE + r'/games/(?P<game_id>[a-zA-Z0-9_]+)/tournament-results/$', ui.project_tournament_results_page, name='project_tournament_results'),
    re_path(r'^' + _PROJECT_ID_RE + r'/games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group_number>\d+)/$', ui.project_task_group_page, name='project_task_group'),
    # Legacy but needed actions/pages referenced by UI (keep inside project prefix).
    re_path(r'^' + _PROJECT_ID_RE + r'/register/(?P<game_id>[a-zA-Z0-9_]+)/$', register_to_game),
    re_path(r'^' + _PROJECT_ID_RE + r'/confirm_user_joining_team/(?P<user_id>\d+)/$', confirm_user_joining_team),
    re_path(r'^' + _PROJECT_ID_RE + r'/reject_user_joining_team/(?P<user_id>\d+)/$', reject_user_joining_team),
    re_path(r'^' + _PROJECT_ID_RE + r'/kick_out_user/(?P<user_id>\d+)/$', kick_out_user),

    # Profile & team under project prefix (same handlers as root /profile/, /team/...).
    re_path(r'^' + _PROJECT_ID_RE + r'/profile/$', ui.profile, name='project_profile'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/$', ui.team, name='project_team'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/create/$', ui.team_create, name='project_team_create'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/join/$', ui.team_join_page, name='project_team_join_page'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/name-check/$', ui.team_name_check, name='project_team_name_check'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/info/$', ui.team_info, name='project_team_info'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/request-join/$', ui.team_request_join, name='project_team_request_join'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/join-by-password/$', ui.team_join_by_password, name='project_team_join_by_password'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/password/$', ui.team_password, name='project_team_password'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/rename/$', ui.team_rename, name='project_team_rename'),
    re_path(r'^' + _PROJECT_ID_RE + r'/team/set-primary/$', ui.team_set_primary, name='project_team_set_primary'),

    path('', ui.hub, name='ui_hub'),
    path('sections/', RedirectView.as_view(url='/', query_string=True), name='ui_sections'),
    path('play-mode/', ui.set_play_mode, name='ui_set_play_mode'),
    path('migrate-anon-attempts/', ui.migrate_anon_attempts, name='ui_migrate_anon_attempts'),
    path('answer/<int:task_id>/', ui.get_answer, name='ui_get_answer'),
    path('answer/<int:task_id>/<int:line_index>/', ui.get_replacements_line_answer, name='ui_get_replacements_line_answer'),
    path('like-dislike/<int:task_id>/', ui.like_dislike, name='ui_like_dislike'),
    path('games/<str:game_id>/', ui.main_game_page, name='ui_main_game'),
    path('games/<str:game_id>/results/', ui.results_page, name='ui_results'),
    path('games/<str:game_id>/tournament-results/', ui.tournament_results_page, name='ui_tournament_results'),
    path('team/name-check/', ui.team_name_check, name='ui_team_name_check'),
    path('team/info/', ui.team_info, name='ui_team_info'),
    path('team/create/', ui.team_create, name='ui_team_create'),
    path('team/request-join/', ui.team_request_join, name='ui_team_request_join'),
    path('team/join-by-password/', ui.team_join_by_password, name='ui_team_join_by_password'),
    path('team/password/', ui.team_password, name='ui_team_password'),
    path('team/rename/', ui.team_rename, name='ui_team_rename'),
    path('games/<str:game_id>/<int:task_group_number>/', ui.task_group_page, name='ui_task_group'),
    path('section/<str:game_id>/results/', ui.section_results_page, name='ui_section_results'),
    path('section/<str:game_id>/', ui.section_game_page, name='ui_section_game'),
    path('profile/', ui.profile, name='ui_profile'),
    path('team/', ui.team, name='ui_team'),
    path('pay/', ui.pay_page, name='ui_pay'),
    path('pay/create-ticket-payment/', ui.create_ticket_payment, name='ui_create_ticket_payment'),
    path('<slug>/', ui.folder, name='ui_folder'),
]
