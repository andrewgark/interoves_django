from django.urls import path

from . import views
from .webhook import telegram_webhook


urlpatterns = [
    path('interoves-telegram/login/', views.interoves_telegram_login, name="interoves_telegram_login"),
    path('webhook/', telegram_webhook, name='telegram_webhook'),
    path('webhook/<str:secret>/', telegram_webhook, name='telegram_webhook_secret'),
]