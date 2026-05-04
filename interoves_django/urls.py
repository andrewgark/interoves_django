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
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include, re_path
from django.shortcuts import redirect
from django.views.generic import RedirectView, TemplateView

from microsites import views as microsites_views
from games.views.meta_http import deploy_version
from games.views.ticket import yookassa_webhook

nutrimatic_patterns = [
    path("", microsites_views.nutrimatic_search, name="nutrimatic_home"),
    re_path(
        r"^(?P<rel_path>[-a-zA-Z0-9_.]+)$",
        microsites_views.nutrimatic_web_file,
    ),
]

eurovision_booklet_patterns = [
    path(
        "",
        RedirectView.as_view(url="/eurovision_booklet/2026/", permanent=False),
    ),
    path(
        "2026/pdf/<str:filename>",
        microsites_views.eurovision_booklet_pdf,
        name="eurovision_booklet_pdf",
    ),
    path(
        "2026/",
        microsites_views.eurovision_booklet_2026,
        name="eurovision_booklet_2026",
    ),
]


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', lambda request: redirect('/?login=1' + ('&next=' + request.GET.get('next') if request.GET.get('next') else ''))),
    path('accounts/', include('allauth.urls')),
    path('logout/', LogoutView.as_view(), name='logout'),

    path('privacy-policy/', lambda request: redirect('https://www.iubenda.com/privacy-policy/73503867')),
    path('terms-of-use/', TemplateView.as_view(template_name="terms-of-use.html")),
    path('tickets/', TemplateView.as_view(template_name="tickets.html")),
    path('ticket-agreement/', TemplateView.as_view(template_name="ticket-agreement.html")),
    path('vpn/', TemplateView.as_view(template_name="new/pigeon_vpn.html"), name='pigeon_vpn'),

    path('yookassa/webhook/', yookassa_webhook, name='yookassa_webhook'),
    path('health/', include('health_check.urls')),
    path('meta/deploy-version/', deploy_version, name='deploy_version'),

    path('inline-edit', include('inlineedit.urls')),

    path('explorer/', include('explorer.urls')),

    path("nutrimatic-ru/", include(nutrimatic_patterns)),
    path("eurovision_booklet/", include(eurovision_booklet_patterns)),

    # Main UI still POSTs to /send_attempt/, links to /games/..., /register/, team moderation URLs.
    path('', include('games.root_shared_urls')),
    path('old/', include('games.old_urls')),
    path('', include('games.ui_urls')),
    path('', include('games.new_urls')),
]

# In development, serve from STATICFILES_DIRS and app static (not only STATIC_ROOT).
# Plain static(STATIC_URL, STATIC_ROOT) misses files that exist only under static/ until collectstatic.
if settings.DEBUG and not getattr(settings, "IS_PROD", False):
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
