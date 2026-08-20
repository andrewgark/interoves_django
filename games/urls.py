from django.urls import path

from .telegram_oidc import telegram_callback, telegram_login


urlpatterns = [
    path("telegram/login/", telegram_login, name="telegram_login"),
    path("telegram/callback/", telegram_callback, name="telegram_callback"),
]
