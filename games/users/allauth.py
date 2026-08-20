import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from games.models import Profile
from django.shortcuts import redirect

from games.account_merge import stash_pending_account_merge


logger = logging.getLogger(__name__)

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

        process = (getattr(sociallogin, 'state', None) or {}).get('process')

        # A successful connect callback proves ownership of the provider
        # identity. If it belongs to another Interoves user, pause allauth's
        # unsupported connect flow and ask for an explicit account merge.
        if (
            process == 'connect'
            and request.user.is_authenticated
            and sociallogin.is_existing
            and sociallogin.user.pk != request.user.pk
        ):
            stash_pending_account_merge(request, sociallogin)
            raise ImmediateHttpResponse(redirect('ui_account_merge_confirm'))

        # Never auto-link by email during `process=connect`: allauth must attach
        # a free identity to request.user, not to some email-matched account.
        if process == 'connect':
            return

        # Auto-link a new social identity only through a unique email address
        # that the provider itself marked verified. User.email is not unique in
        # this project, and an unverified address is not proof of ownership.
        if sociallogin.is_existing:
            return

        # A Telegram identity verified earlier through the bot-link flow is
        # strong ownership proof. Reuse that profile when the first OIDC login
        # arrives, instead of creating a second user without an email.
        if sociallogin.account.provider == 'telegram':
            try:
                telegram_user_id = int(sociallogin.account.uid)
            except (TypeError, ValueError):
                telegram_user_id = None
            if telegram_user_id is not None:
                profile = Profile.objects.select_related('user').filter(
                    telegram_user_id=telegram_user_id,
                    telegram_verified=True,
                    user__is_active=True,
                ).first()
                if profile is not None:
                    sociallogin.connect(request, profile.user)
                    return

        verified_emails = {
            (address.email or '').strip().lower()
            for address in (sociallogin.email_addresses or [])
            if address.verified and address.email
        }
        if len(verified_emails) != 1:
            return
        email = next(iter(verified_emails))
        user_ids = list(
            EmailAddress.objects.filter(email__iexact=email, verified=True)
            .values_list('user_id', flat=True)
            .distinct()[:2]
        )
        if len(user_ids) != 1:
            return

        existing_address = EmailAddress.objects.filter(
            user_id=user_ids[0], email__iexact=email, verified=True,
        ).select_related('user').first()
        if existing_address is None:
            return
        existing_user = existing_address.user
        if not existing_user.is_active:
            return
        sociallogin.connect(request, existing_user)

    def on_authentication_error(
        self, request, provider, error=None, exception=None, extra_context=None,
    ):
        logger.warning(
            'Social account authentication failed: provider=%s error=%s exception=%s',
            getattr(provider, 'id', provider),
            error,
            exception.__class__.__name__ if exception is not None else None,
        )
