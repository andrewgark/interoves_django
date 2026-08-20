from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from allauth.socialaccount.models import SocialAccount

from games.models import Profile
from games.telegram_oidc import TelegramProvider


class TelegramOIDCTests(TestCase):
    def test_provider_uses_stable_sub_and_profile_fields(self):
        provider = object.__new__(TelegramProvider)
        self.assertEqual(provider.extract_uid({'sub': 12345}), '12345')
        self.assertEqual(
            provider.extract_common_fields({'given_name': 'Андрей', 'family_name': 'Г.'}),
            {'first_name': 'Андрей', 'last_name': 'Г.'},
        )

    def test_social_account_syncs_verified_telegram_identity(self):
        user = get_user_model().objects.create_user(username='tg-user')
        Profile.objects.create(user=user, first_name='', last_name='')
        SocialAccount.objects.create(
            user=user,
            provider='telegram',
            uid='987654',
            extra_data={'preferred_username': 'andrew'},
        )
        profile = Profile.objects.get(user=user)
        self.assertEqual(profile.telegram_user_id, 987654)
        self.assertTrue(profile.telegram_verified)
        self.assertEqual(profile.telegram_username, 'andrew')

    @patch('games.telegram_oidc.jwtkit.verify_and_decode')
    def test_id_token_is_verified_against_telegram_jwks(self, verify):
        verify.return_value = {'sub': '42', 'given_name': 'Tg'}
        adapter = object.__new__(__import__('games.telegram_oidc', fromlist=['TelegramOIDCAdapter']).TelegramOIDCAdapter)
        provider = object.__new__(TelegramProvider)
        provider.app = type('App', (), {'client_id': 'client-id'})()
        adapter.get_provider = lambda: provider
        request = object()
        with patch.object(provider, 'sociallogin_from_response', return_value='login') as build:
            result = adapter.complete_login(request, provider.app, None, {'id_token': 'signed'})
        self.assertEqual(result, 'login')
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs['audience'], 'client-id')
        self.assertEqual(verify.call_args.kwargs['issuer'], 'https://oauth.telegram.org')
