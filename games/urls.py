"""Allauth imports ``games.urls`` for the Telegram OIDC provider (mounted under ``/accounts/``)."""

from games.identity.telegram_oidc import urlpatterns

__all__ = ["urlpatterns"]
