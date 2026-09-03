import json
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from games.account_merge import (
    AccountMergeError,
    PENDING_ACCOUNT_MERGE_SESSION_KEY,
    build_account_merge_preview,
    merge_accounts,
)
from games.models import (
    AccountMerge,
    AnonAccountClaim,
    AlphabettyPersonalDictWord,
    Attempt,
    BugReport,
    CheckerType,
    ChainTaskState,
    Game,
    HTMLPage,
    Hint,
    HintAttempt,
    Like,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    ProfileTeamMembership,
    Project,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)
from games.users.allauth import SocialAccountAdapter


class AccountMergeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        site, _ = Site.objects.get_or_create(
            id=1, defaults={'domain': 'testserver', 'name': 'test'},
        )
        for provider, name in (('google', 'Google'), ('vk', 'VK')):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)
        checker, _ = CheckerType.objects.get_or_create(pk='equals')
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        cls.game = Game.objects.create(
            id='account_merge_test',
            name='Merge test',
            author='test',
            author_extra='',
            project_id='main',
            is_ready=True,
        )
        cls.task_group = TaskGroup.objects.create(label='account_merge_group')
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.task_group,
                number='1',
                checker=checker,
                answer='ok',
                points=1,
            )
            cls.hint = Hint.objects.create(task=cls.task, number='1', points_penalty=0)

    def setUp(self):
        self.target = User.objects.create_user(
            username='merge-target', email='target@example.com', password='secret',
        )
        self.source = User.objects.create_user(
            username='merge-source', email='source@example.com', password='secret',
        )
        self.target_team = Team.objects.create(name='merge-target-team', project_id='main')
        self.source_team = Team.objects.create(name='merge-source-team', project_id='main')
        Profile.objects.create(
            user=self.target,
            first_name='Target',
            last_name='Person',
            timezone='Asia/Yerevan',
            telegram_handle='target_tg',
            team_on=self.target_team,
        )
        Profile.objects.create(
            user=self.source,
            first_name='Source',
            last_name='Person',
            timezone='Europe/Moscow',
            telegram_handle='source_tg',
            team_on=self.source_team,
        )
        self.google = SocialAccount.objects.create(
            user=self.target, provider='google', uid='google-target',
            extra_data={'email': 'target@example.com'},
        )
        self.vk = SocialAccount.objects.create(
            user=self.source, provider='vk', uid='vk-source',
            extra_data={'screen_name': 'source'},
        )

    def test_connecting_second_social_account_preserves_profile(self):
        SocialAccount.objects.create(
            user=self.target,
            provider='vk',
            uid='vk-target-new',
            extra_data={
                'first_name': 'OAuth',
                'last_name': 'Overwrite',
                'screen_name': 'oauth',
            },
        )

        profile = Profile.objects.get(user=self.target)
        self.assertEqual(profile.first_name, 'Target')
        self.assertEqual(profile.last_name, 'Person')
        self.assertEqual(profile.timezone, 'Asia/Yerevan')
        self.assertEqual(profile.telegram_handle, 'target_tg')
        self.assertEqual(profile.team_on, self.target_team)

    def test_adapter_stashes_merge_when_provider_belongs_to_other_user(self):
        request = RequestFactory().get('/accounts/vk/login/callback/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request.user = self.target
        sociallogin = SocialLogin(user=self.source, account=self.vk)
        sociallogin.state = {'process': 'connect', 'next': '/profile/'}

        with self.assertRaises(ImmediateHttpResponse) as caught:
            SocialAccountAdapter().pre_social_login(request, sociallogin)

        self.assertEqual(caught.exception.response.url, reverse('ui_account_merge_confirm'))
        pending = request.session[PENDING_ACCOUNT_MERGE_SESSION_KEY]
        self.assertEqual(pending['target_user_id'], self.target.pk)
        self.assertEqual(pending['source_user_id'], self.source.pk)
        self.assertEqual(pending['provider'], 'vk')

    def test_adapter_links_only_unique_verified_email_on_login(self):
        EmailAddress.objects.create(
            user=self.target,
            email='verified@example.com',
            verified=True,
            primary=True,
        )
        sociallogin = SocialLogin(
            user=User(username='temporary', email='verified@example.com'),
            account=SocialAccount(provider='google', uid='new-google'),
            email_addresses=[EmailAddress(email='verified@example.com', verified=True)],
        )
        sociallogin.state = {'process': 'login'}
        request = RequestFactory().get('/')
        request.user = self.source

        with patch.object(sociallogin, 'connect') as connect:
            SocialAccountAdapter().pre_social_login(request, sociallogin)

        connect.assert_called_once_with(request, self.target)

    def test_adapter_does_not_link_unverified_email(self):
        EmailAddress.objects.create(
            user=self.target,
            email='unverified@example.com',
            verified=False,
            primary=True,
        )
        sociallogin = SocialLogin(
            user=User(username='temporary', email='unverified@example.com'),
            account=SocialAccount(provider='google', uid='new-google'),
            email_addresses=[EmailAddress(email='unverified@example.com', verified=False)],
        )
        sociallogin.state = {'process': 'login'}
        request = RequestFactory().get('/')
        request.user = self.source

        with patch.object(sociallogin, 'connect') as connect:
            SocialAccountAdapter().pre_social_login(request, sociallogin)

        connect.assert_not_called()

    def test_merge_moves_data_and_preserves_target_profile(self):
        with patch('games.views.track.track_task_change'):
            attempt = Attempt.manager.create(
                user=self.source, task=self.task, game=self.game, text='x', status='Wrong',
            )
        hint_attempt = HintAttempt.objects.create(user=self.source, hint=self.hint)
        Like.manager.create(user=self.target, task=self.task, value=-1)
        Like.manager.create(user=self.source, task=self.task, value=1)
        AlphabettyPersonalDictWord.objects.create(user=self.target, word='СЛОВО')
        AlphabettyPersonalDictWord.objects.create(user=self.source, word='СЛОВО')
        report = BugReport.objects.create(
            user=self.source, task=self.task, game=self.game, text='report',
        )
        claim = AnonAccountClaim.objects.create(
            anon_key='source-claimed-anon', user=self.source,
        )
        EmailAddress.objects.create(
            user=self.target, email='target@example.com', verified=True, primary=True,
        )
        EmailAddress.objects.create(
            user=self.source, email='source@example.com', verified=False, primary=True,
        )

        merge = merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        self.assertIsInstance(merge, AccountMerge)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)
        self.assertFalse(self.source.has_usable_password())
        self.assertEqual(SocialAccount.objects.get(pk=self.vk.pk).user, self.target)
        self.assertEqual(Attempt.manager.get(pk=attempt.pk).user, self.target)
        self.assertEqual(HintAttempt.objects.get(pk=hint_attempt.pk).user, self.target)
        self.assertEqual(BugReport.objects.get(pk=report.pk).user, self.target)
        self.assertEqual(AnonAccountClaim.objects.get(pk=claim.pk).user, self.target)
        self.assertSetEqual(
            set(EmailAddress.objects.filter(user=self.target).values_list('email', flat=True)),
            {'target@example.com', 'source@example.com'},
        )
        self.assertEqual(EmailAddress.objects.filter(user=self.target, primary=True).count(), 1)
        self.assertEqual(Like.manager.filter(user=self.target, task=self.task).count(), 1)
        self.assertEqual(Like.manager.get(user=self.target, task=self.task).value, -1)
        self.assertEqual(
            AlphabettyPersonalDictWord.objects.filter(user=self.target, word='СЛОВО').count(),
            1,
        )

        profile = Profile.objects.get(user=self.target)
        self.assertEqual(profile.first_name, 'Target')
        self.assertEqual(profile.timezone, 'Asia/Yerevan')
        self.assertEqual(profile.telegram_handle, 'target_tg')
        self.assertEqual(profile.team_on, self.target_team)
        self.assertSetEqual(
            set(ProfileTeamMembership.objects.filter(profile=profile).values_list('team_id', flat=True)),
            {self.target_team.pk, self.source_team.pk},
        )
        self.assertFalse(
            ProfileTeamMembership.objects.filter(profile__user=self.source).exists(),
        )

    def test_merge_analytics_uses_earlier_complete_provenance_bundles(self):
        now = timezone.now()
        instance_id = '{}:{}'.format(self.game.pk, self.task_group.pk)
        target_start = PlayerStartedGame.objects.create(
            user=self.target,
            game=self.game,
            task_group=self.task_group,
            game_kind='target-start',
            game_instance_id=instance_id,
            public_game_id='target-start-public',
            is_backfilled=False,
            instrumentation_version=2,
        )
        source_start = PlayerStartedGame.objects.create(
            user=self.source,
            game=self.game,
            task_group=self.task_group,
            game_kind='source-start',
            game_instance_id=instance_id,
            public_game_id='source-start-public',
            is_backfilled=True,
            instrumentation_version=None,
        )
        target_completion = PlayerCompletedGame.objects.create(
            user=self.target,
            game=self.game,
            task_group=self.task_group,
            game_kind='target-completion',
            game_instance_id=instance_id,
            public_game_id='target-completion-public',
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=False,
            instrumentation_version=2,
        )
        source_completion = PlayerCompletedGame.objects.create(
            user=self.source,
            game=self.game,
            task_group=self.task_group,
            game_kind='source-completion',
            game_instance_id=instance_id,
            public_game_id='source-completion-public',
            result=PlayerCompletedGame.RESULT_FAILED,
            is_backfilled=True,
            instrumentation_version=None,
        )
        PlayerStartedGame.objects.filter(pk=target_start.pk).update(
            started_at=now - timedelta(minutes=5),
        )
        PlayerStartedGame.objects.filter(pk=source_start.pk).update(
            started_at=now - timedelta(minutes=10),
        )
        PlayerCompletedGame.objects.filter(pk=target_completion.pk).update(
            completed_at=now - timedelta(minutes=5),
        )
        PlayerCompletedGame.objects.filter(pk=source_completion.pk).update(
            completed_at=now - timedelta(minutes=10),
        )
        target_state = PlayerAnalyticsState.objects.create(
            user=self.target,
            signup_at=now - timedelta(days=10),
            signup_method='email',
            activated_at=now - timedelta(days=2),
            activation_is_backfilled=False,
        )
        PlayerAnalyticsState.objects.create(
            user=self.source,
            signup_at=now - timedelta(days=5),
            signup_method='vk',
            activated_at=now - timedelta(days=8),
            activation_is_backfilled=True,
        )

        merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        start = PlayerStartedGame.objects.get(pk=target_start.pk)
        self.assertEqual(start.started_at, now - timedelta(minutes=10))
        self.assertEqual(start.game_kind, 'source-start')
        self.assertTrue(start.is_backfilled)
        self.assertIsNone(start.instrumentation_version)
        completion = PlayerCompletedGame.objects.get(pk=target_completion.pk)
        self.assertEqual(completion.completed_at, now - timedelta(minutes=10))
        self.assertEqual(completion.result, PlayerCompletedGame.RESULT_FAILED)
        self.assertEqual(completion.game_kind, 'source-completion')
        self.assertTrue(completion.is_backfilled)
        self.assertIsNone(completion.instrumentation_version)
        state = PlayerAnalyticsState.objects.get(pk=target_state.pk)
        self.assertEqual(state.signup_at, now - timedelta(days=10))
        self.assertEqual(state.signup_method, 'email')
        self.assertEqual(state.activated_at, now - timedelta(days=8))
        self.assertTrue(state.activation_is_backfilled)

    def test_different_accounts_of_same_provider_block_merge(self):
        SocialAccount.objects.create(
            user=self.source, provider='google', uid='google-source',
        )
        preview = build_account_merge_preview(self.target, self.source)
        self.assertFalse(preview['can_merge'])
        self.assertIn('provider:google', preview['conflicts'])

        with self.assertRaises(AccountMergeError):
            merge_accounts(
                target_user=self.target,
                source_user=self.source,
                provider='vk',
                provider_uid='vk-source',
            )

    @skipUnless(hasattr(Profile, 'telegram_user_id'), 'Telegram identity is not installed')
    def test_different_verified_telegram_identities_block_merge(self):
        target_profile = self.target.profile
        target_profile.telegram_user_id = 1001
        target_profile.telegram_verified = True
        target_profile.save(update_fields=['telegram_user_id', 'telegram_verified'])
        source_profile = self.source.profile
        source_profile.telegram_user_id = 1002
        source_profile.telegram_verified = True
        source_profile.save(update_fields=['telegram_user_id', 'telegram_verified'])

        preview = build_account_merge_preview(self.target, self.source)

        self.assertFalse(preview['can_merge'])
        self.assertIn('telegram_identity_conflict', preview['conflicts'])

    @skipUnless(hasattr(Profile, 'telegram_user_id'), 'Telegram identity is not installed')
    def test_source_verified_telegram_identity_moves_when_target_has_none(self):
        source_profile = self.source.profile
        source_profile.telegram_user_id = 1002
        source_profile.telegram_username = 'source_verified'
        source_profile.telegram_verified = True
        source_profile.save(update_fields=[
            'telegram_user_id', 'telegram_username', 'telegram_verified',
        ])

        merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        target_profile = Profile.objects.get(user=self.target)
        source_profile.refresh_from_db()
        self.assertEqual(target_profile.telegram_user_id, 1002)
        self.assertEqual(target_profile.telegram_username, 'source_verified')
        self.assertTrue(target_profile.telegram_verified)
        self.assertIsNone(source_profile.telegram_user_id)
        self.assertFalse(source_profile.telegram_verified)

    def test_merge_reassigns_payment_records_and_invalidates_source_link_tokens(self):
        from django.apps import apps

        try:
            TributePaymentIntent = apps.get_model('games', 'TributePaymentIntent')
            TributePurchase = apps.get_model('games', 'TributePurchase')
            TelegramLinkToken = apps.get_model('games', 'TelegramLinkToken')
        except LookupError:
            self.skipTest('Tribute/Telegram payment models are not installed')
        ticket_request = TicketRequest.objects.create(
            team=self.source_team,
            created_by=self.source,
            money=10,
            tickets=1,
        )
        intent = TributePaymentIntent.objects.create(
            user=self.source,
            team=self.source_team,
            ticket_request=ticket_request,
            telegram_user_id=1002,
            expected_product_id=501,
            expected_amount=1000,
            expected_currency='EUR',
            ticket_type=TributePaymentIntent.TYPE_REGULAR,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        purchase = TributePurchase.objects.create(
            purchase_id='merge-purchase',
            matched_user=self.source,
        )
        TelegramLinkToken.objects.create(
            user=self.source,
            token_hash='source-link-token',
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        self.assertEqual(TributePaymentIntent.objects.get(pk=intent.pk).user, self.target)
        self.assertEqual(TributePurchase.objects.get(pk=purchase.pk).matched_user, self.target)
        self.assertFalse(TelegramLinkToken.objects.filter(user=self.source).exists())

    def test_merge_promotes_shared_verified_email_without_constraint_error(self):
        EmailAddress.objects.create(
            user=self.target, email='shared@example.com', verified=False, primary=True,
        )
        EmailAddress.objects.create(
            user=self.source, email='shared@example.com', verified=True, primary=True,
        )

        merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        rows = EmailAddress.objects.filter(
            user=self.target, email='shared@example.com',
        )
        self.assertEqual(rows.count(), 1)
        self.assertTrue(rows.get().verified)

    def test_merge_unions_disjoint_replacements_progress(self):
        self.task.task_type = 'replacements_lines'
        self.task.save(update_fields=['task_type'])
        ChainTaskState.objects.create(
            user=self.target,
            task=self.task,
            game=self.game,
            game_mode='general',
            state=json.dumps({'solved_lines': [0], 'total': 1}),
        )
        ChainTaskState.objects.create(
            user=self.source,
            task=self.task,
            game=self.game,
            game_mode='general',
            state=json.dumps({'solved_lines': [1], 'total': 1}),
        )

        merge_accounts(
            target_user=self.target,
            source_user=self.source,
            provider='vk',
            provider_uid='vk-source',
        )

        state = ChainTaskState.objects.get(
            user=self.target, task=self.task, game=self.game, game_mode='general',
        )
        self.assertEqual(json.loads(state.state), {'solved_lines': [0, 1], 'total': 2})
        self.assertFalse(ChainTaskState.objects.filter(user=self.source).exists())

    def test_confirm_view_merges_after_matching_nonce(self):
        client = Client()
        client.force_login(self.target)
        session = client.session
        session[PENDING_ACCOUNT_MERGE_SESSION_KEY] = {
            'target_user_id': self.target.pk,
            'source_user_id': self.source.pk,
            'provider': 'vk',
            'provider_uid': 'vk-source',
            'created_at': __import__('time').time(),
            'nonce': 'merge-nonce',
            'next': '/profile/',
        }
        session.save()

        response = client.post(reverse('ui_account_merge_confirm'), {
            'action': 'merge',
            'nonce': 'merge-nonce',
        })

        self.assertRedirects(response, '/profile/', fetch_redirect_response=False)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)
        self.assertNotIn(PENDING_ACCOUNT_MERGE_SESSION_KEY, client.session)

    def test_confirm_view_renders_preview_before_merge(self):
        client = Client()
        client.force_login(self.target)
        session = client.session
        session[PENDING_ACCOUNT_MERGE_SESSION_KEY] = {
            'target_user_id': self.target.pk,
            'source_user_id': self.source.pk,
            'provider': 'vk',
            'provider_uid': 'vk-source',
            'created_at': __import__('time').time(),
            'nonce': 'preview-nonce',
            'next': '/profile/',
        }
        session.save()

        response = client.get(reverse('ui_account_merge_confirm'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Объединить профили?')
        self.assertContains(response, 'Source Person')

    def test_disconnect_keeps_other_login_and_profile(self):
        second = SocialAccount.objects.create(
            user=self.target, provider='vk', uid='vk-target-disconnect',
        )
        client = Client()
        client.force_login(self.target)

        response = client.post(reverse('ui_social_account_disconnect'), {
            'account_id': second.pk,
            'next': '/profile/',
        })

        self.assertRedirects(response, '/profile/', fetch_redirect_response=False)
        self.assertFalse(SocialAccount.objects.filter(pk=second.pk).exists())
        self.assertTrue(SocialAccount.objects.filter(pk=self.google.pk).exists())
        profile = Profile.objects.get(user=self.target)
        self.assertEqual(profile.team_on, self.target_team)
        self.assertEqual(profile.telegram_handle, 'target_tg')

    def test_disconnect_rejects_last_login_method(self):
        client = Client()
        client.force_login(self.target)

        response = client.post(reverse('ui_social_account_disconnect'), {
            'account_id': self.google.pk,
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SocialAccount.objects.filter(pk=self.google.pk).exists())

    def test_social_login_cancel_and_error_use_local_ui(self):
        client = Client()
        cancelled = client.get(reverse('socialaccount_login_cancelled'))
        error = client.get(reverse('socialaccount_login_error'))

        self.assertEqual(cancelled.status_code, 200)
        self.assertContains(cancelled, 'Вход отменён')
        self.assertEqual(error.status_code, 200)
        self.assertContains(error, 'Не удалось войти')
