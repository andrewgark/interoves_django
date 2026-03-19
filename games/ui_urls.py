from django.urls import path

from games.views import ui

urlpatterns = [
    path('', ui.hub, name='ui_hub'),
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
    path('section/<str:game_id>/', ui.section_game_page, name='ui_section_game'),
    path('profile/', ui.profile, name='ui_profile'),
    path('team/', ui.team, name='ui_team'),
    path('pay/', ui.pay_page, name='ui_pay'),
    path('pay/create-ticket-payment/', ui.create_ticket_payment, name='ui_create_ticket_payment'),
    path('<slug>/', ui.folder, name='ui_folder'),
]
