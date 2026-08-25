"""Telegram Login OIDC provider.

Telegram's modern Login flow is a regular OAuth 2.0 authorization-code flow
with PKCE.  The existing bot deep-link flow remains separate and is used for
linking a Telegram chat to an already authenticated profile.
"""

import requests

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.internal import jwtkit
from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider
from allauth.socialaccount.providers.oauth2.views import (
    OAuth2Adapter,
    OAuth2CallbackView,
    OAuth2LoginView,
)


TELEGRAM_AUTHORIZE_URL = "https://oauth.telegram.org/auth"
TELEGRAM_ACCESS_TOKEN_URL = "https://oauth.telegram.org/token"
TELEGRAM_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"


class TelegramOIDCAccount(ProviderAccount):
    def to_str(self):
        return self.account.extra_data.get("username") or self.account.uid


def telegram_user_id_from_claims(claims):
    """Return Telegram's numeric user ID from OIDC profile claims.

    ``sub`` is the stable OIDC subject used as the allauth SocialAccount UID;
    Telegram's Bot API-compatible numeric ID is provided separately as ``id``.
    """
    try:
        value = int((claims or {}).get("id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class TelegramOIDCAdapter(OAuth2Adapter):
    provider_id = "telegram"
    access_token_url = TELEGRAM_ACCESS_TOKEN_URL
    authorize_url = TELEGRAM_AUTHORIZE_URL
    # Telegram's token endpoint requires HTTP Basic auth with the BotFather
    # client id and secret (rather than credentials in the form body).
    basic_auth = True

    def complete_login(self, request, app, token, response, **kwargs):
        id_token = response.get("id_token")
        if not id_token:
            raise OAuth2Error("Telegram did not return an id_token")
        try:
            data = jwtkit.verify_and_decode(
                credential=id_token,
                keys_url=TELEGRAM_JWKS_URL,
                issuer=TELEGRAM_ISSUER,
                audience=app.client_id,
                lookup_kid=jwtkit.lookup_kid_jwk,
            )
        except (OAuth2Error, requests.RequestException) as exc:
            raise OAuth2Error("Invalid Telegram id_token") from exc
        return self.get_provider().sociallogin_from_response(request, data)


class TelegramProvider(OAuth2Provider):
    id = "telegram"
    name = "Telegram"
    account_class = TelegramOIDCAccount
    oauth2_adapter_class = TelegramOIDCAdapter
    pkce_enabled_default = True

    def get_default_scope(self):
        return ["openid", "profile"]

    def extract_uid(self, data):
        return str(data["sub"])

    def extract_common_fields(self, data):
        return {
            "first_name": data.get("given_name") or data.get("first_name"),
            "last_name": data.get("family_name") or data.get("last_name"),
        }


class TelegramLoginView(OAuth2LoginView):
    pass


class TelegramCallbackView(OAuth2CallbackView):
    pass


telegram_login = TelegramLoginView.adapter_view(TelegramOIDCAdapter)
telegram_callback = TelegramCallbackView.adapter_view(TelegramOIDCAdapter)


provider_classes = [TelegramProvider]
