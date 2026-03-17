from django.urls import path

from games.views import new_ui

urlpatterns = [
    path('', new_ui.new_hub, name='new_hub'),
    path('play-mode/', new_ui.new_set_play_mode, name='new_set_play_mode'),
    path('migrate-anon-attempts/', new_ui.new_migrate_anon_attempts, name='new_migrate_anon_attempts'),
    path('answer/<int:task_id>/', new_ui.new_get_answer, name='new_get_answer'),
    path('like-dislike/<int:task_id>/', new_ui.new_like_dislike, name='new_like_dislike'),
    path('games/<str:game_id>/', new_ui.new_main_game_page, name='new_main_game'),
    path('games/<str:game_id>/results/', new_ui.new_results_page, name='new_results'),
    path('games/<str:game_id>/tournament-results/', new_ui.new_tournament_results_page, name='new_tournament_results'),
    path('team/name-check/', new_ui.new_team_name_check, name='new_team_name_check'),
    path('team/info/', new_ui.new_team_info, name='new_team_info'),
    path('team/create/', new_ui.new_team_create, name='new_team_create'),
    path('team/request-join/', new_ui.new_team_request_join, name='new_team_request_join'),
    path('team/join-by-password/', new_ui.new_team_join_by_password, name='new_team_join_by_password'),
    path('team/password/', new_ui.new_team_password, name='new_team_password'),
    path('team/rename/', new_ui.new_team_rename, name='new_team_rename'),
    path('games/<str:game_id>/<int:task_group_number>/', new_ui.new_task_group_page, name='new_task_group'),
    path('section/<str:game_id>/', new_ui.new_section_game_page, name='new_section_game'),
    path('profile/', new_ui.new_profile, name='new_profile'),
    path('team/', new_ui.new_team, name='new_team'),
    path('<slug>/', new_ui.new_folder, name='new_folder'),
]
