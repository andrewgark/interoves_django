from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model


class AccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
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
