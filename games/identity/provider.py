"""Custom allauth providers shipped by the games application."""

from games.identity.telegram_oidc import TelegramProvider

provider_classes = [TelegramProvider]
