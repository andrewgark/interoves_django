from django.urls import path

from . import views


urlpatterns = [
    path('interoves-telegram/login/', views.interoves_telegram_login, name="interoves_telegram_login")
]