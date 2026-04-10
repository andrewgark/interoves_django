from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

# Glowbyte UI (/glowbyte/...) — только Google и только корпоративная почта (см. also base.html).
GLOWBYTE_OAUTH_PATH_MARKER = '/glowbyte'
GLOWBYTE_GOOGLE_EMAIL_SUFFIX = '@glowbyteconsulting.com'


def _oauth_next_targets_glowbyte(sociallogin) -> bool:
    state = getattr(sociallogin, 'state', None) or {}
    next_url = state.get('next') or ''
    if not isinstance(next_url, str):
        return False
    return GLOWBYTE_OAUTH_PATH_MARKER in next_url


class AccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        if _oauth_next_targets_glowbyte(sociallogin):
            provider = sociallogin.account.provider
            if provider == 'vk':
                raise ImmediateHttpResponse(redirect('/glowbyte/?auth=vk_not_allowed'))
            if provider == 'google':
                email = ((sociallogin.user and sociallogin.user.email) or '').strip().lower()
                if not email.endswith(GLOWBYTE_GOOGLE_EMAIL_SUFFIX.lower()):
                    raise ImmediateHttpResponse(redirect('/glowbyte/?auth=email_not_allowed'))

        # Auto-link social account to an existing user with the same email
        if sociallogin.is_existing:
            return

        email = (sociallogin.user and sociallogin.user.email) or None
        if not email:
            return

        UserModel = get_user_model()
        try:
            existing_user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            return

        sociallogin.connect(request, existing_user)

    def authentication_error(self, request, provider_id, error, exception, extra_context):
        print(
            'SocialAccount authentication error!',
            'error',
            {'provider_id': provider_id, 'error': error.__str__(), 'exception': exception.__str__(), 'extra_context': extra_context},
        )
