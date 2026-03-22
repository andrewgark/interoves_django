"""Backward-compatible aliases for previous `new_*` route names."""

from django.urls import path

from games.views import ui

urlpatterns = [
    path('', ui.hub, name='new_hub'),
    path('play-mode/', ui.set_play_mode, name='new_set_play_mode'),
    path('migrate-anon-attempts/', ui.migrate_anon_attempts, name='new_migrate_anon_attempts'),
    path('answer/<int:task_id>/', ui.get_answer, name='new_get_answer'),
    path('answer/<int:task_id>/<int:line_index>/', ui.get_replacements_line_answer, name='new_get_replacements_line_answer'),
    path('like-dislike/<int:task_id>/', ui.like_dislike, name='new_like_dislike'),
    path('games/<str:game_id>/', ui.main_game_page, name='new_main_game'),
    path('games/<str:game_id>/results/', ui.results_page, name='new_results'),
    path('games/<str:game_id>/tournament-results/', ui.tournament_results_page, name='new_tournament_results'),
    path('team/name-check/', ui.team_name_check, name='new_team_name_check'),
    path('team/info/', ui.team_info, name='new_team_info'),
    path('team/create/', ui.team_create, name='new_team_create'),
    path('team/request-join/', ui.team_request_join, name='new_team_request_join'),
    path('team/join-by-password/', ui.team_join_by_password, name='new_team_join_by_password'),
    path('team/password/', ui.team_password, name='new_team_password'),
    path('team/rename/', ui.team_rename, name='new_team_rename'),
    path('team/set-primary/', ui.team_set_primary, name='new_team_set_primary'),
    path('team/join/', ui.team_join_page, name='new_team_join_page'),
    path('games/<str:game_id>/<int:task_group_number>/', ui.task_group_page, name='new_task_group'),
    path('section/<str:game_id>/results/', ui.section_results_page, name='new_section_results'),
    path('section/<str:game_id>/', ui.section_game_page, name='new_section_game'),
    path('profile/', ui.profile, name='new_profile'),
    path('team/', ui.team, name='new_team'),
    path('pay/', ui.pay_page, name='new_pay'),
    path('pay/create-ticket-payment/', ui.create_ticket_payment, name='new_create_ticket_payment'),
    path('<slug>/', ui.folder, name='new_folder'),
]
