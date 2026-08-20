"""Custom allauth providers shipped by the games application."""

from .telegram_oidc import TelegramProvider


provider_classes = [TelegramProvider]
