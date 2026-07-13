from django.contrib.auth.views import LogoutView
from django.urls import path

from games.support import actions
from games.support import views

app_name = 'support'

urlpatterns = [
    path('login/', views.SupportLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='support:login'), name='logout'),
    path('', views.hub, name='hub'),
    path('games/', views.games_browse, name='games'),
    path('sections/', views.sections_dashboard, name='sections'),
    path('stats/', views.stats_dashboard, name='stats'),
    path('pending/', views.pending_queue, name='pending'),
    path('live/', views.live_dashboard, name='live'),
    path('live/feed.json', views.live_feed_json, name='live_feed_json'),
    path('chain/attempt/<int:attempt_id>/', views.chain_attempt, name='chain'),
    path('action/', actions.perform_action, name='action'),
    path('actor/team/<str:team_name>/', views.actor_team, name='actor_team'),
    path('actor/user/<int:user_id>/', views.actor_user, name='actor_user'),
    path('actor/anon/<str:anon_key>/', views.actor_anon, name='actor_anon'),
    path('game/<str:game_id>/', views.game_dashboard, name='game'),
    path('preview/games/<str:game_id>/', views.preview_game, name='preview_game'),
    path(
        'preview/games/<str:game_id>/<str:task_group_number>/',
        views.preview_task_group,
        name='preview_task_group',
    ),
]
