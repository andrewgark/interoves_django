"""Custom allauth providers shipped by the games application."""

from games.club.telegram_oidc import TelegramProvider


provider_classes = [TelegramProvider]
