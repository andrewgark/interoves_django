from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def authentication_error(self, request, provider_id, error, exception, extra_context):
        print(
            'SocialAccount authentication error!',
            'error',
            {'provider_id': provider_id, 'error': error.__str__(), 'exception': exception.__str__(), 'extra_context': extra_context},
        )
