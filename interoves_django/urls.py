"""interoves_django URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls import url
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.contrib.auth.views import LogoutView
from games.views.views import MainPageView, game_page, results_page, \
                        create_team, join_team, quit_from_team, \
                        confirm_user_joining_team, reject_user_joining_team, \
                        send_attempt, send_hint_attempt, get_answer, like_dislike, \
                        get_tournament_results, return_intentional_503
from games.views.ticket import request_ticket
from games.views.registration import register_to_game


urlpatterns = [
    path('', MainPageView.as_view(project_name='main')),
    path('main/', MainPageView.as_view(project_name='main')),
    path('glowbyte/', MainPageView.as_view(project_name='glowbyte')),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('logout/', LogoutView.as_view(), name='logout'),

    path('privacy-policy/', TemplateView.as_view(template_name="privacy-policy.html")),
    path('terms-of-use/', TemplateView.as_view(template_name="terms-of-use.html")),

    path('create_team/', create_team, name='create_team'),
    path('join_team/', join_team, name='join_team'),
    path('quit_from_team/', quit_from_team, name='quit_from_team'),
    url(r'^confirm_user_joining_team/(?P<user_id>\d+)/$', confirm_user_joining_team),
    url(r'^reject_user_joining_team/(?P<user_id>\d+)/$', reject_user_joining_team),

    url(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/$', game_page),
    url(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)$', game_page),
    url(r'^games/(?P<game_id>[a-zA-Z0-9_]+)/(?P<task_group>[0-9]+)/(?P<task>[0-9.a-zA-Zа-яА-Я]+)$', game_page),

    url(r'^send_attempt/(?P<task_id>\d+)/$', send_attempt),
    url(r'^send_hint_attempt/(?P<task_id>\d+)/$', send_hint_attempt),
    url(r'^get_answer/(?P<task_id>\d+)/$', get_answer),
    url(r'^like_dislike/(?P<task_id>\d+)/', like_dislike),

    url(r'^register/(?P<game_id>[a-zA-Z0-9_]+)/$', register_to_game),
    url(r'^request_ticket/', request_ticket, name='request_ticket'),

    url(r'^results/(?P<game_id>[a-zA-Z0-9_]+)/$', results_page),
    url(r'^tournament_results/(?P<game_id>[a-zA-Z0-9_]+)/$', get_tournament_results),

    url('cw_hint', return_intentional_503),

    url(r'^health/?', include('health_check.urls')),

    path('inline-edit', include('inlineedit.urls')),

    path('explorer/', include('explorer.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
