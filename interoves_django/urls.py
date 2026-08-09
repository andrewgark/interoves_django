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
from games.views.ticket import nowpayments_ipn, tribute_webhook, yookassa_webhook
from games.views.order_game_landing import order_game_landing
from games.views.instagram_feed import instagram_feed, ladder_teaser_jpg
from games.social.views import social_queue_instagram_jpg
from games.telegram.urls import urlpatterns as telegram_urlpatterns
from games.views import ui as ui_views
from games.views.legal import legal_page

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
        "assets/<path:relpath>",
        microsites_views.eurovision_booklet_shared_assets,
        name="eurovision_booklet_assets",
    ),
    path(
        "2026/pdf/<str:filename>",
        microsites_views.eurovision_booklet_pdf,
        name="eurovision_booklet_pdf",
    ),
    path(
        "2026/html/<slug:slug>/<path:relpath>",
        microsites_views.eurovision_booklet_html_bundle,
        name="eurovision_booklet_html_asset",
    ),
    path(
        "2026/html/<slug:slug>/",
        microsites_views.eurovision_booklet_html_bundle,
        kwargs={"relpath": "index.html"},
        name="eurovision_booklet_html",
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

    path('sellers/', legal_page, {'document': 'sellers'}, name='sellers'),
    path('terms/', legal_page, {'document': 'terms'}, name='terms'),
    path('terms/russia/', legal_page, {'document': 'terms_russia'}, name='terms_russia'),
    path('terms/armenia/', legal_page, {'document': 'terms_armenia'}, name='terms_armenia'),
    path('terms/crypto/', legal_page, {'document': 'terms_crypto'}, name='terms_crypto'),
    path('refunds/', legal_page, {'document': 'refunds'}, name='refunds'),
    path('privacy/', legal_page, {'document': 'privacy'}, name='privacy'),
    path('contacts/', legal_page, {'document': 'contacts'}, name='contacts'),
    re_path(r'^privacy-policy/?$', RedirectView.as_view(url='/privacy/', permanent=True, query_string=True)),
    re_path(r'^terms-of-use/?$', RedirectView.as_view(url='/terms/', permanent=True, query_string=True)),
    path('tickets/', RedirectView.as_view(url='/pay/', permanent=True), name='legacy_tickets'),
    re_path(r'^ticket-agreement/?$', RedirectView.as_view(url='/terms/russia/', permanent=True, query_string=True)),
    path('vpn/', TemplateView.as_view(template_name="new/pigeon_vpn.html"), name='pigeon_vpn'),
    path('donate/', ui_views.donate_page, name='donate'),
    path('donate/create-crypto-payment/', ui_views.create_crypto_donation, name='donate_create_crypto'),
    path('donate/status/<str:public_token>/', ui_views.donation_status, name='donate_status'),
    path('instagram/', instagram_feed, name='instagram_feed'),
    path('ladder/<int:number>/teaser.jpg', ladder_teaser_jpg, name='ladder_teaser_jpg'),
    path(
        'social/queue/<int:pk>/instagram.jpg',
        social_queue_instagram_jpg,
        name='social_queue_instagram_jpg',
    ),
    path('order-game/', order_game_landing, name='order_game_landing'),
    path('corporate/', RedirectView.as_view(url='/order-game/', permanent=True), name='corporate_landing'),

    path('yookassa/webhook/', yookassa_webhook, name='yookassa_webhook'),
    path('nowpayments/ipn/', nowpayments_ipn, name='nowpayments_ipn'),
    path('tribute/webhook/', tribute_webhook, name='tribute_webhook'),
    path('telegram/', include(telegram_urlpatterns)),
    path('health/', include('health_check.urls')),
    path('meta/deploy-version/', deploy_version, name='deploy_version'),

    path('inline-edit', include('inlineedit.urls')),

    path('explorer/', include('explorer.urls')),

    path('support/', include('games.support.urls')),

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
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
    if getattr(settings, 'MEDIA_ROOT', None):
        urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
