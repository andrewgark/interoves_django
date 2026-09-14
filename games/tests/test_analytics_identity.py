from datetime import timedelta
from io import StringIO

from allauth.account.signals import user_signed_up
from allauth.socialaccount.models import SocialApp
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from games.analytics_identity import (
    ANON_COOKIE_NAME,
    ANON_SIG_COOKIE_NAME,
    attach_anon_cookie,
    attach_unsigned_anon_cookie,
    gameplay_anon_key,
    is_valid_anon_key,
    signature_is_valid,
    stamp_anon_identity,
)
from games.models import (
    AnonAccountClaim,
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    Project,
    Task,
    TaskGroup,
)


def _ensure_social_apps():
    site = Site.objects.get_current()
    for provider in ('google', 'vk', 'yandex'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class AnalyticsIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_social_apps()
        Project.objects.get_or_create(pk='main', defaults={})
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Ladder',
                'author': 'test',
                'project_id': 'main',
                'is_ready': True,
            },
        )
        cls.task_group = TaskGroup.objects.create(label='identity-tg')
        GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.task_group,
            number='1',
            name='One',
        )
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            checker_id='equals',
            answer='ok',
        )

    def _started(self, **actor):
        return PlayerStartedGame.objects.create(
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id='{}:{}'.format(self.game.id, self.task_group.pk),
            instrumentation_version=2,
            **actor,
        )

    def test_health_and_meta_paths_do_not_issue_identity(self):
        response = self.client.get('/health/live/')
        self.assertNotIn(ANON_COOKIE_NAME, response.cookies)
        self.assertNotIn(ANON_SIG_COOKIE_NAME, response.cookies)
        meta = self.client.get('/meta/deploy-version/')
        self.assertNotIn(ANON_COOKIE_NAME, meta.cookies)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        key = response.cookies[ANON_COOKIE_NAME].value
        sig = response.cookies[ANON_SIG_COOKIE_NAME].value
        self.assertTrue(is_valid_anon_key(key))
        self.assertTrue(signature_is_valid(key, sig))
        self.assertTrue(response.cookies[ANON_SIG_COOKIE_NAME].get('httponly'))

        again = self.client.get('/')
        self.assertEqual(again.cookies.get(ANON_COOKIE_NAME), None)
        self.assertEqual(self.client.cookies[ANON_COOKIE_NAME].value, key)

    def test_same_cookies_reuse_identity(self):
        first = self.client.get('/')
        key = first.cookies[ANON_COOKIE_NAME].value
        other = Client()
        other.cookies[ANON_COOKIE_NAME] = key
        other.cookies[ANON_SIG_COOKIE_NAME] = first.cookies[ANON_SIG_COOKIE_NAME].value
        second = other.get('/')
        self.assertEqual(second.status_code, 200)
        self.assertNotIn(ANON_COOKIE_NAME, second.cookies)
        self.assertEqual(other.cookies[ANON_COOKIE_NAME].value, key)

    def test_malformed_cookie_issues_fresh_identity_not_500(self):
        self.client.cookies[ANON_COOKIE_NAME] = '???'
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        key = response.cookies[ANON_COOKIE_NAME].value
        self.assertTrue(is_valid_anon_key(key))
        self.assertNotEqual(key, '???')

    def test_invalid_signature_issues_fresh_identity(self):
        key = attach_anon_cookie(self.client, 'signed-then-tampered-key')
        self._started(anon_key=key)
        self.client.cookies[ANON_SIG_COOKIE_NAME] = '0' * 64
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        issued = response.cookies[ANON_COOKIE_NAME].value
        self.assertNotEqual(issued, key)
        self.assertTrue(signature_is_valid(issued, response.cookies[ANON_SIG_COOKIE_NAME].value))

    def test_unsigned_unknown_cookie_is_not_adopted(self):
        key = attach_unsigned_anon_cookie(self.client, 'legacy-unsigned-cookie-key')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        issued = response.cookies[ANON_COOKIE_NAME].value
        self.assertNotEqual(issued, key)
        self.assertTrue(is_valid_anon_key(issued))
        self.assertTrue(signature_is_valid(issued, response.cookies[ANON_SIG_COOKIE_NAME].value))

    def test_unsigned_legacy_cookie_with_history_is_adopted_and_signed(self):
        key = 'legacy-unsigned-with-history'
        start = self._started(anon_key=key)
        attach_unsigned_anon_cookie(self.client, key)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ANON_COOKIE_NAME, response.cookies)
        self.assertEqual(self.client.cookies[ANON_COOKIE_NAME].value, key)
        sig = response.cookies[ANON_SIG_COOKIE_NAME].value
        self.assertTrue(signature_is_valid(key, sig))
        start.refresh_from_db()
        self.assertEqual(start.anon_key, key)
        self.assertIsNone(start.user_id)

    def test_header_and_post_and_query_cannot_steal_another_actor(self):
        victim = 'victim-anon-identity-key'
        self._started(anon_key=victim)
        attacker = Client()
        attach_anon_cookie(attacker, 'attacker-anon-identity-key')
        response = attacker.get(
            '/?anon={}&anon_key={}'.format(victim, victim),
            HTTP_X_INTEROVES_ANON=victim,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(attacker.cookies[ANON_COOKIE_NAME].value, 'attacker-anon-identity-key')

        factory = RequestFactory()
        request = factory.post(
            '/send_attempt/1/',
            {'anon_key': victim},
            HTTP_X_INTEROVES_ANON=victim,
        )
        request.user = AnonymousUser()
        stamp_anon_identity(request, 'attacker-anon-identity-key')
        self.assertEqual(gameplay_anon_key(request), 'attacker-anon-identity-key')

        stolen = factory.post(
            '/send_attempt/1/',
            {'anon_key': victim},
            HTTP_X_INTEROVES_ANON=victim,
        )
        stolen.user = AnonymousUser()
        self.assertNotEqual(gameplay_anon_key(stolen), victim)

    def test_signup_auto_claims_current_cookie_without_copying(self):
        key = 'signup-auto-claim-key'
        start = self._started(anon_key=key)
        user = User.objects.create_user('signup-claim-user', 'signup-claim@example.com', 'x')
        request = RequestFactory().post('/accounts/signup/')
        stamp_anon_identity(request, key)
        request.user = user
        user_signed_up.send(sender=User, request=request, user=user)
        start.refresh_from_db()
        self.assertEqual(start.user_id, user.pk)
        self.assertIsNone(start.anon_key)
        self.assertEqual(PlayerStartedGame.objects.filter(pk=start.pk).count(), 1)
        self.assertEqual(AnonAccountClaim.objects.get(anon_key=key).user_id, user.pk)

        user_signed_up.send(sender=User, request=request, user=user)
        self.assertEqual(PlayerStartedGame.objects.filter(user=user).count(), 1)
        self.assertEqual(AnonAccountClaim.objects.filter(anon_key=key).count(), 1)

    def test_existing_account_login_does_not_auto_claim(self):
        key = 'login-no-auto-claim-key'
        start = self._started(anon_key=key)
        user = User.objects.create_user('login-no-claim', 'login-no-claim@example.com', 'x')
        Profile.objects.create(user=user, first_name='L', last_name='N')
        client = Client()
        attach_anon_cookie(client, key)
        self.assertTrue(client.login(username='login-no-claim', password='x'))
        start.refresh_from_db()
        self.assertIsNone(start.user_id)
        self.assertEqual(start.anon_key, key)
        self.assertFalse(AnonAccountClaim.objects.filter(anon_key=key).exists())

    def test_explicit_claim_requires_matching_cookie(self):
        key = 'explicit-claim-cookie-key'
        self._started(anon_key=key)
        user = User.objects.create_user('claim-user', 'claim-user@example.com', 'x')
        Profile.objects.create(user=user, first_name='C', last_name='U')
        client = Client()
        self.assertTrue(client.login(username='claim-user', password='x'))
        url = reverse('new_migrate_anon_attempts')

        missing = client.post(url, {'anon_key': key})
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json()['status'], 'anon_key_mismatch')
        self.assertTrue(PlayerStartedGame.objects.filter(anon_key=key).exists())
        self.assertFalse(AnonAccountClaim.objects.filter(anon_key=key).exists())

        attach_anon_cookie(client, 'other-browser-claim-key')
        foreign = client.post(url, {'anon_key': key})
        self.assertEqual(foreign.status_code, 403)
        self.assertFalse(AnonAccountClaim.objects.filter(anon_key=key).exists())

        header_only = Client()
        header_only.login(username='claim-user', password='x')
        header_resp = header_only.post(
            url,
            {'anon_key': key},
            HTTP_X_INTEROVES_ANON=key,
        )
        self.assertEqual(header_resp.status_code, 403)

        attach_anon_cookie(client, key)
        ok = client.post(url, {'anon_key': key})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['status'], 'ok')
        self.assertFalse(PlayerStartedGame.objects.filter(anon_key=key).exists())
        self.assertTrue(PlayerStartedGame.objects.filter(user=user, anon_key__isnull=True).exists())

    def test_claimed_elsewhere_still_conflicts(self):
        key = 'claimed-elsewhere-key'
        self._started(anon_key=key)
        owner = User.objects.create_user('claim-owner', 'owner@example.com', 'x')
        other = User.objects.create_user('claim-other', 'other@example.com', 'x')
        Profile.objects.create(user=owner, first_name='O', last_name='W')
        Profile.objects.create(user=other, first_name='T', last_name='H')
        AnonAccountClaim.objects.create(anon_key=key, user=owner)
        client = Client()
        self.assertTrue(client.login(username='claim-other', password='x'))
        attach_anon_cookie(client, key)
        response = client.post(reverse('new_migrate_anon_attempts'), {'anon_key': key})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'claimed_elsewhere')
        self.assertTrue(PlayerStartedGame.objects.filter(anon_key=key).exists())

    def test_logout_rotates_anon_identity(self):
        user = User.objects.create_user('logout-rotator', 'logout-rot@example.com', 'x')
        Profile.objects.create(user=user, first_name='L', last_name='R')
        client = Client()
        old_key = attach_anon_cookie(client, 'pre-logout-anon-key')
        self.assertTrue(client.login(username='logout-rotator', password='x'))
        response = client.post('/logout/')
        self.assertEqual(response.status_code, 302)
        new_key = client.cookies[ANON_COOKIE_NAME].value
        self.assertNotEqual(new_key, old_key)
        self.assertTrue(is_valid_anon_key(new_key))
        self.assertTrue(
            signature_is_valid(new_key, client.cookies[ANON_SIG_COOKIE_NAME].value)
        )
        self._started(anon_key=new_key)
        self.assertFalse(PlayerStartedGame.objects.filter(user=user).exists())
        self.assertFalse(PlayerStartedGame.objects.filter(anon_key=old_key).exists())
        self.assertTrue(PlayerStartedGame.objects.filter(anon_key=new_key).exists())

    def test_two_accounts_sequential_do_not_share_anon_history(self):
        first = User.objects.create_user('seq-a', 'seq-a@example.com', 'x')
        second = User.objects.create_user('seq-b', 'seq-b@example.com', 'x')
        Profile.objects.create(user=first, first_name='A', last_name='A')
        Profile.objects.create(user=second, first_name='B', last_name='B')
        client = Client()
        first_key = attach_anon_cookie(client, 'shared-browser-first-key')
        self._started(anon_key=first_key)
        self.assertTrue(client.login(username='seq-a', password='x'))
        self._started(user=first)
        client.post('/logout/')
        new_key = client.cookies[ANON_COOKIE_NAME].value
        self.assertNotEqual(new_key, first_key)
        self.assertTrue(client.login(username='seq-b', password='x'))
        self._started(user=second)
        self.assertEqual(
            PlayerStartedGame.objects.filter(user=first).count(),
            1,
        )
        self.assertEqual(
            PlayerStartedGame.objects.filter(user=second).count(),
            1,
        )
        self.assertTrue(PlayerStartedGame.objects.filter(anon_key=first_key).exists())
        self.assertFalse(PlayerStartedGame.objects.filter(user=second, anon_key=first_key).exists())

    def test_registered_activity_aggregates_by_user_across_clients(self):
        user = User.objects.create_user('multi-device', 'multi-device@example.com', 'x')
        Profile.objects.create(user=user, first_name='M', last_name='D')
        a = Client()
        b = Client()
        a.force_login(user)
        b.force_login(user)
        self._started(user=user)
        self.assertEqual(PlayerStartedGame.objects.filter(user=user).count(), 1)
        self.assertEqual(a.session['_auth_user_id'], str(user.pk))
        self.assertEqual(b.session['_auth_user_id'], str(user.pk))


class AnalyticsIdentityConcurrencyTests(TransactionTestCase):
    def setUp(self):
        _ensure_social_apps()

    def test_parallel_first_requests_do_not_500(self):
        first = Client()
        second = Client()
        r1 = first.get('/')
        r2 = second.get('/')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(is_valid_anon_key(r1.cookies[ANON_COOKIE_NAME].value))
        self.assertTrue(is_valid_anon_key(r2.cookies[ANON_COOKIE_NAME].value))


class AnalyticsIdentityQualityWarnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.user = User.objects.create_user(username='identity-qc-user')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Ladder',
                'author': 'test',
                'project': cls.project,
                'requires_ticket': False,
                'is_tournament': False,
                'is_ready': True,
            },
        )
        cls.task_group = TaskGroup.objects.create(label='identity-qc-tg')
        GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.task_group,
            number='1',
            name='One',
        )

    def _bounds(self):
        now = timezone.now()
        return now - timedelta(hours=1), now + timedelta(hours=1)

    def test_legacy_completion_without_start_is_warning_only(self):
        PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id='ladder:{}'.format(self.task_group.pk),
            is_backfilled=True,
            instrumentation_version=None,
        )
        since, until = self._bounds()
        out = StringIO()
        call_command(
            'check_product_analytics',
            '--since', since.isoformat(),
            '--until', until.isoformat(),
            stdout=out,
        )
        report = out.getvalue()
        self.assertIn('completion_without_start\tPASS\t0', report)
        self.assertIn('completion_without_start_legacy\tWARN\t1', report)

    def test_claimed_anon_writes_are_warnings(self):
        key = 'claimed-then-written-key'
        AnonAccountClaim.objects.create(anon_key=key, user=self.user)
        PlayerStartedGame.objects.create(
            anon_key=key,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id='ladder:{}'.format(self.task_group.pk),
            instrumentation_version=2,
        )
        since, until = self._bounds()
        out = StringIO()
        call_command(
            'check_product_analytics',
            '--since', since.isoformat(),
            '--until', until.isoformat(),
            stdout=out,
        )
        self.assertIn('anon_writes_after_claim\tWARN\t1', out.getvalue())
