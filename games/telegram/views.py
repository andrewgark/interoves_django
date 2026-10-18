import logging

import requests

from django.core.exceptions import ImproperlyConfigured

from allauth.socialaccount import app_settings, providers
from allauth.socialaccount.helpers import (
    complete_social_login,
    render_authentication_error,
)
from allauth.socialaccount.models import SocialLogin

from .provider import InterovesTelegramProvider

logger = logging.getLogger('application')
TELEGRAM_LOGIN_TIMEOUT = (3.05, 8)


def interoves_telegram_login(request):
    # resp = requests.post('https://verifier.login.persona.org/verify',
    #                      {'assertion': assertion,
    #                       'audience': audience})
    settings = app_settings.PROVIDERS.get(InterovesTelegramProvider.id, {})

    try:
        resp = requests.post(
            'https://oauth.telegram.org/embed/interoves_bot', {
                'origin': settings['domain'],
                'size': settings['size'],
                'request_access': settings['request_access'],
            },
            timeout=TELEGRAM_LOGIN_TIMEOUT,
        )
        resp.raise_for_status()
    except (ValueError, requests.RequestException) as e:
        logger.warning('Telegram social login endpoint failed: %s', e.__class__.__name__)
        return render_authentication_error(
            request,
            provider_id=InterovesTelegramProvider.id,
            exception=e
        )
    login = providers.registry \
        .by_id(InterovesTelegramProvider.id, request) \
        .sociallogin_from_response(request, {})
    login.state = SocialLogin.state_from_request(request)
    return complete_social_login(request, login)
