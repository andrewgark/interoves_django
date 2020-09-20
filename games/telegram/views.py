import requests

from django.core.exceptions import ImproperlyConfigured

from allauth.socialaccount import app_settings, providers
from allauth.socialaccount.helpers import (
    complete_social_login,
    render_authentication_error,
)
from allauth.socialaccount.models import SocialLogin

from .provider import InterovesTelegramProvider


def interoves_telegram_login(request):
    # resp = requests.post('https://verifier.login.persona.org/verify',
    #                      {'assertion': assertion,
    #                       'audience': audience})
    settings = app_settings.PROVIDERS.get(InterovesTelegramProvider.id, {})

    resp = requests.post(
        'https://oauth.telegram.org/embed/interoves_bot', {
            'origin': settings['domain'],
            'size': settings['size'],
            'request_access': settings['request_access'],
        }
    )
    print(resp)

    try:
        resp.raise_for_status()
    except (ValueError, requests.RequestException) as e:
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
