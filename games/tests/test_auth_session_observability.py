"""Focused tests for Stage 1 auth/session diagnostics."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.core import signing
from django.http import JsonResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from games.auth_observability import (
    log_auth_event,
    log_startup_auth_configuration,
    session_fingerprint,
)
from games.middleware.auth_session_observability import (
    AuthSessionDiagnosticMiddleware,
    RequestCorrelationMiddleware,
    _inspect_persisted_session,
)


TEST_FINGERPRINT_KEY = 'test-only-auth-log-fingerprint-key-with-high-entropy'


@override_settings(AUTH_LOG_FINGERPRINT_KEY=TEST_FINGERPRINT_KEY)
class AuthSessionObservabilityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username='auth-observability-user',
            email='auth-observability@example.invalid',
            password='old-password',
        )

    def _new_auth_session(self, user=None, *, backend=None, auth_hash=None):
        user = user or self.user
        store = SessionStore()
        store[SESSION_KEY] = str(user.pk)
        store[BACKEND_SESSION_KEY] = backend or settings.AUTHENTICATION_BACKENDS[0]
        store[HASH_SESSION_KEY] = (
            user.get_session_auth_hash() if auth_hash is None else auth_hash
        )
        store.save()
        return store.session_key

    def _dispatch(self, session_key=None, path='/auth-diagnostic/'):
        observed = {}

        def view(request):
            observed['is_authenticated'] = request.user.is_authenticated
            observed['user_id'] = request.user.pk if request.user.is_authenticated else None
            return JsonResponse(observed)

        handler = RequestCorrelationMiddleware(
            SessionMiddleware(
                AuthSessionDiagnosticMiddleware(
                    AuthenticationMiddleware(view),
                ),
            ),
        )
        kwargs = {'HTTP_HOST': 'interoves.com'}
        if session_key is not None:
            kwargs['HTTP_COOKIE'] = '{}={}'.format(
                settings.SESSION_COOKIE_NAME,
                session_key,
            )
        request = self.factory.get(path, **kwargs)
        response = handler(request)
        return response, observed

    @staticmethod
    def _anomaly_payload(log_context):
        payloads = [json.loads(record.getMessage()) for record in log_context.records]
        return next(item for item in payloads if item['event'] == 'auth_session_anomaly')

    def test_login_then_normal_authenticated_request_has_no_anomaly(self):
        client = Client()
        with self.assertLogs('interoves.auth', level='INFO') as login_logs:
            self.assertTrue(client.login(
                username=self.user.username,
                password='old-password',
            ))
        login_payload = json.loads(login_logs.records[-1].getMessage())
        self.assertEqual(login_payload['event'], 'login')
        self.assertEqual(login_payload['user_id'], str(self.user.pk))
        self.assertNotEqual(login_payload['session_expires_at'], 'unavailable')

        session_key = client.cookies[settings.SESSION_COOKIE_NAME].value
        with self.assertNoLogs('interoves.auth', level='INFO'):
            response, observed = self._dispatch(session_key)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(observed['is_authenticated'])
        self.assertEqual(observed['user_id'], self.user.pk)

    def test_shared_db_session_is_accepted_by_another_instance(self):
        session_key = self._new_auth_session()
        with override_settings(INSTANCE_ID='instance-a'):
            _, first = self._dispatch(session_key)
        with override_settings(INSTANCE_ID='instance-b'):
            _, second = self._dispatch(session_key)
        self.assertTrue(first['is_authenticated'])
        self.assertTrue(second['is_authenticated'])

    def test_healthy_authenticated_request_has_no_diagnostic_query(self):
        session_key = self._new_auth_session()
        # One indexed session read + one user read: the same work required by
        # Django AuthenticationMiddleware when request.user is evaluated.
        with self.assertNumQueries(2):
            _, observed = self._dispatch(session_key)
        self.assertTrue(observed['is_authenticated'])

    def test_missing_session_row_is_logged(self):
        session_key = 'a' * 32
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            _, observed = self._dispatch(session_key)
        payload = self._anomaly_payload(logs)
        self.assertFalse(observed['is_authenticated'])
        self.assertEqual(payload['classification'], 'session_row_missing')
        self.assertEqual(payload['session_fingerprint'], session_fingerprint(session_key))

    def test_expired_session_is_logged(self):
        store = SessionStore()
        store['anonymous_state'] = True
        store.save()
        Session.objects.filter(session_key=store.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1),
        )
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(store.session_key)
        self.assertEqual(
            self._anomaly_payload(logs)['classification'],
            'session_expired',
        )

    def test_tampered_signed_session_is_logged(self):
        session_key = 'b' * 32
        Session.objects.create(
            session_key=session_key,
            session_data='not-a-valid-signed-session',
            expire_date=timezone.now() + timedelta(days=1),
        )
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(session_key)
        self.assertEqual(
            self._anomaly_payload(logs)['classification'],
            'signature_mismatch',
        )

    def test_signed_non_mapping_session_is_decode_failed(self):
        session_key = 'c' * 32
        store = SessionStore(session_key=session_key)
        session_data = signing.dumps(
            ['not', 'a', 'mapping'],
            salt=store.key_salt,
            serializer=store.serializer,
        )
        Session.objects.create(
            session_key=session_key,
            session_data=session_data,
            expire_date=timezone.now() + timedelta(days=1),
        )
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            with self.assertRaises(TypeError):
                self._dispatch(session_key)
        self.assertEqual(
            self._anomaly_payload(logs)['classification'],
            'session_decode_failed',
        )

    def test_inactive_user_is_logged(self):
        session_key = self._new_auth_session()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(session_key)
        payload = self._anomaly_payload(logs)
        self.assertEqual(payload['classification'], 'user_inactive')
        self.assertEqual(payload['user_id'], str(self.user.pk))

    def test_auth_hash_invalidation_is_logged(self):
        session_key = self._new_auth_session()
        self.user.set_password('new-password')
        self.user.save(update_fields=['password'])
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(session_key)
        payload = self._anomaly_payload(logs)
        self.assertEqual(payload['classification'], 'auth_hash_mismatch')
        self.assertEqual(payload['user_id'], str(self.user.pk))

    def test_missing_user_is_logged(self):
        session_key = self._new_auth_session()
        user_id = self.user.pk
        self.user.delete()
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(session_key)
        payload = self._anomaly_payload(logs)
        self.assertEqual(payload['classification'], 'user_missing')
        self.assertEqual(payload['user_id'], str(user_id))

    def test_invalid_auth_backend_is_logged(self):
        session_key = self._new_auth_session(backend='missing.auth.Backend')
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            self._dispatch(session_key)
        self.assertEqual(
            self._anomaly_payload(logs)['classification'],
            'auth_backend_invalid',
        )

    def test_explicit_logout_is_logged_and_session_removed(self):
        client = Client()
        client.force_login(self.user)
        session_key = client.cookies[settings.SESSION_COOKIE_NAME].value
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            response = client.post('/logout/')
        payloads = [json.loads(record.getMessage()) for record in logs.records]
        payload = next(item for item in payloads if item['event'] == 'logout')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(payload['logout_reason'], 'explicit_logout')
        self.assertEqual(payload['user_id'], str(self.user.pk))
        self.assertEqual(payload['session_fingerprint'], session_fingerprint(session_key))
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_missing_cookie_is_classifiable_but_normal_anonymous_is_not_logged(self):
        self.assertEqual(
            _inspect_persisted_session(None),
            ('session_cookie_missing', None),
        )
        with self.assertNoLogs('interoves.auth', level='INFO'):
            _, observed = self._dispatch(None)
        self.assertFalse(observed['is_authenticated'])

    def test_request_id_is_returned_and_available_to_auth_log(self):
        session_key = 'd' * 32
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            response, _ = self._dispatch(session_key)
        payload = self._anomaly_payload(logs)
        self.assertRegex(response['X-Request-ID'], r'^[0-9a-f]{32}$')
        self.assertEqual(payload['request_id'], response['X-Request-ID'])

    def test_logs_never_contain_raw_session_cookie_query_or_secrets(self):
        raw_session = 'raw-session-cookie-must-not-appear'
        csrf = 'csrf-token-must-not-appear'
        oauth_code = 'oauth-code-must-not-appear'
        request = self.factory.get(
            '/accounts/provider/login/callback/?code={}'.format(oauth_code),
            HTTP_HOST='interoves.com',
            HTTP_COOKIE='{}={}; csrftoken={}'.format(
                settings.SESSION_COOKIE_NAME,
                raw_session,
                csrf,
            ),
        )
        request.interoves_request_id = 'safe-request-id'
        with self.assertLogs('interoves.auth', level='INFO') as logs:
            log_auth_event(
                'auth_session_anomaly',
                request,
                classification='session_row_missing',
                session_fingerprint=session_fingerprint(raw_session),
            )
            log_startup_auth_configuration()
        output = '\n'.join(record.getMessage() for record in logs.records)
        self.assertNotIn(raw_session, output)
        self.assertNotIn(csrf, output)
        self.assertNotIn(oauth_code, output)
        self.assertNotIn(TEST_FINGERPRINT_KEY, output)
        self.assertNotIn(settings.SECRET_KEY, output)

    def test_fingerprint_is_deterministic_truncated_hmac(self):
        raw = 'session-key-for-fingerprint-test'
        fingerprint = session_fingerprint(raw)
        self.assertEqual(fingerprint, session_fingerprint(raw))
        self.assertEqual(len(fingerprint), 20)
        self.assertNotEqual(fingerprint, raw)

    @override_settings(AUTH_LOG_FINGERPRINT_KEY='')
    def test_fingerprint_has_no_secret_key_fallback(self):
        self.assertEqual(session_fingerprint('raw-session-value'), 'unavailable')


class AuthSessionConfigurationTests(TestCase):
    def test_cookie_security_tracks_production_without_proxy_redirect_settings(self):
        self.assertEqual(settings.SESSION_COOKIE_SECURE, settings.IS_PROD)
        self.assertEqual(settings.CSRF_COOKIE_SECURE, settings.IS_PROD)
        self.assertIsNone(settings.SECURE_PROXY_SSL_HEADER)
        self.assertFalse(settings.SECURE_SSL_REDIRECT)

    def test_session_lifetime_and_scope_are_unchanged(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 14 * 24 * 60 * 60)
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertFalse(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertIsNone(settings.SESSION_COOKIE_DOMAIN)
        self.assertEqual(settings.SESSION_COOKIE_PATH, '/')

    def test_nginx_canonical_redirect_is_permanent_and_preserves_uri(self):
        config = (
            Path(settings.BASE_DIR)
            / '.platform/nginx/conf.d/elasticbeanstalk/00_canonical_host.conf'
        ).read_text(encoding='utf-8')
        self.assertIn('if ($host = www.interoves.com)', config)
        self.assertIn('return 308 https://interoves.com$request_uri;', config)
        self.assertNotIn('if ($host = interoves.com)', config)

    def test_nginx_access_log_has_safe_correlation_fields(self):
        timing_config = (
            Path(settings.BASE_DIR)
            / '.platform/nginx/conf.d/interoves_timing_logformat.conf'
        ).read_text(encoding='utf-8')
        main_config = (
            Path(settings.BASE_DIR) / '.platform/nginx/nginx.conf'
        ).read_text(encoding='utf-8')
        self.assertIn('$host', timing_config)
        self.assertIn('$request_id', timing_config)
        self.assertIn('$upstream_http_x_request_id', timing_config)
        self.assertIn('$hostname', timing_config)
        self.assertIn('$request_method $uri $server_protocol', timing_config)
        self.assertIn('$request_method $uri $server_protocol', main_config)
        self.assertNotIn('"$request"', timing_config)
        self.assertNotIn('"$request"', main_config)
        self.assertNotIn('$http_referer', timing_config + main_config)
        self.assertNotIn('$http_cookie', timing_config + main_config)
        self.assertNotIn('$http_authorization', timing_config + main_config)
