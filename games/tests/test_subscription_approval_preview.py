"""Staff screenshot preview: presentation-only, with no billing side effects."""
import mimetypes
import os
import re
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from games.club_yookassa import renew_due_subscriptions
from games.models import ClubSubscription, ClubYooKassaPayment, SavedPaymentMethod
from games.tests.test_club_yookassa import _ensure_reference_rows


@override_settings(
    CLUB_SUBSCRIPTION_ENABLED=False,
    CLUB_YOOKASSA_ENABLED=False,
    YOOKASSA_RECURRING_ENABLED=False,
)
class SubscriptionApprovalPreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.staff = User.objects.create_user('approval-staff', is_staff=True)
        cls.superuser = User.objects.create_user('approval-superuser', is_superuser=True, is_staff=False)
        cls.member = User.objects.create_user('approval-member')

    def setUp(self):
        self.url = reverse('new_subscription')
        self.preview_url = self.url + '?approval_preview=1'

    def assert_no_preview(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get('approval_preview'))
        self.assertNotContains(response, 'data-approval-preview="1"')
        self.assertNotContains(response, 'Банковская карта •••• 4242')

    def test_anonymous_parameter_is_ignored(self):
        self.assert_no_preview(self.client.get(self.preview_url))

    def test_member_parameter_is_ignored(self):
        self.client.force_login(self.member)
        self.assert_no_preview(self.client.get(self.preview_url))

    def test_staff_and_superuser_can_preview_without_profile_or_billing_flags(self):
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.preview_url)
                self.assertContains(response, 'Банковская карта •••• 4242')
                self.assertContains(response, 'Отвязать карту?')
                self.assertContains(response, 'data-approval-preview="1"')
                self.assertNotContains(response, reverse('new_subscription_payment_method_detach'))
                self.assertNotRegex(response.content.decode(), r'<form\b[^>]*data-sub-yk')
                self.assertNotContains(response, 'Пока недоступно')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('private', response['Cache-Control'])

    def test_staff_requires_exact_parameter(self):
        self.client.force_login(self.staff)
        for suffix in ('', '?approval_preview=0', '?approval_preview=true'):
            with self.subTest(suffix=suffix):
                self.assert_no_preview(self.client.get(self.url + suffix))

    def test_preview_creates_no_billing_rows_and_uses_no_provider(self):
        self.client.force_login(self.staff)
        with patch('games.club_yookassa.Payment.create') as create, \
                patch('games.club_yookassa.Payment.find_one') as find, \
                patch('games.views.subscription.queue_pending_goal') as analytics:
            self.client.get(self.preview_url)
            self.client.get(self.preview_url)
        self.assertFalse(SavedPaymentMethod.objects.exists())
        self.assertFalse(ClubSubscription.objects.exists())
        self.assertFalse(ClubYooKassaPayment.objects.exists())
        create.assert_not_called()
        find.assert_not_called()
        analytics.assert_not_called()
        self.assertFalse(settings.CLUB_YOOKASSA_ENABLED)
        self.assertFalse(settings.YOOKASSA_RECURRING_ENABLED)

    def make_existing_method(self):
        # Only a test-database fixture; preview itself never creates model rows.
        method = SavedPaymentMethod.objects.create(
            user=self.staff, provider_payment_method_id='existing-test-credential', card_last4='9876',
        )
        ClubSubscription.objects.create(
            user=self.staff, provider='yookassa', plan='monthly', status='active',
            saved_payment_method=method, auto_renew=True,
            paid_until=timezone.now() + timedelta(days=10), next_charge_at=timezone.now(),
        )

    @staticmethod
    def billing_snapshot():
        return [list(model.objects.order_by('pk').values()) for model in (
            SavedPaymentMethod, ClubSubscription, ClubYooKassaPayment,
        )]

    def test_existing_method_is_not_loaded_or_changed_and_recurring_stays_disabled(self):
        self.make_existing_method()
        self.client.force_login(self.staff)
        before = self.billing_snapshot()
        with patch('games.views.subscription.SavedPaymentMethod.objects.filter',
                   side_effect=AssertionError('Preview must not load payment methods')), \
                patch('games.club_yookassa.Payment.create') as create:
            response = self.client.get(self.preview_url)
            self.assertEqual(renew_due_subscriptions()['created'], 0)
        create.assert_not_called()
        self.assertNotContains(response, 'existing-test-credential')
        self.assertNotContains(response, '9876')
        self.assertEqual(self.billing_snapshot(), before)
        self.assertFalse(settings.CLUB_YOOKASSA_ENABLED)
        self.assertFalse(settings.YOOKASSA_RECURRING_ENABLED)

    def test_preview_form_fallback_is_safe_get_and_real_endpoint_remains_post_only(self):
        self.make_existing_method()
        self.client.force_login(self.staff)
        before = self.billing_snapshot()
        response = self.client.get(self.preview_url)
        form = re.search(r'<form id="new-sub-detach-form"(.*?)</form>',
                         response.content.decode(), re.DOTALL).group(1)
        self.assertIn('method="get"', form)
        self.assertIn('action="{}"'.format(self.url), form)
        self.assertIn('name="approval_preview" value="1"', form)
        self.client.get(self.url, {'approval_preview': '1'})
        response = self.client.get(reverse('new_subscription_payment_method_detach'), {'approval_preview': '1'})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.billing_snapshot(), before)

    @skipUnless(os.environ.get('INTEROVES_APPROVAL_PREVIEW_BROWSER_TESTS') == '1',
                'Set INTEROVES_APPROVAL_PREVIEW_BROWSER_TESTS=1 to run Chromium verification.')
    def test_browser_preview_detach_changes_only_dom_without_post_or_provider_calls(self):
        from playwright.sync_api import expect, sync_playwright
        self.make_existing_method()
        self.client.force_login(self.staff)
        before = self.billing_snapshot()
        with patch('games.club_yookassa.Payment.create') as create, \
                patch('games.club_yookassa.Payment.find_one') as find, \
                patch('games.views.subscription.detach_yookassa_payment_method') as detach:
            html = self.client.get(self.preview_url).content
            requests = []

            def route_request(route):
                request = route.request
                requests.append((request.method, request.url))
                url = urlsplit(request.url)
                if url.hostname == 'testserver' and url.path == self.url:
                    return route.fulfill(body=html, content_type='text/html')
                if url.hostname == 'testserver' and url.path.startswith('/static/'):
                    path = finders.find(url.path[len('/static/'):])
                    if path:
                        return route.fulfill(body=Path(path).read_bytes(),
                                             content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return route.fulfill(status=204, body='')

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(timeout=15000)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    page.route('**/*', route_request)  # No actual network traffic.
                    page.goto('http://testserver' + self.preview_url)
                    expect(page.get_by_text('Банковская карта •••• 4242', exact=True)).to_be_visible()
                    expect(page.locator('#new-sub-detached')).to_be_hidden()
                    for width in (560, 800, 1000, 1440):
                        page.set_viewport_size({'width': width, 'height': 1000})
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.locator('#new-sub-detach-open').click()
                    expect(page.locator('#new-sub-detach-modal')).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(page.locator('#new-sub-detach-modal')).to_be_hidden()
                    page.locator('#new-sub-detach-open').click()
                    page.locator('#new-sub-detach-form [data-detach-close]').click()
                    expect(page.locator('#new-sub-payment-method')).to_be_visible()
                    page.locator('#new-sub-detach-open').click()
                    page.locator('#new-sub-detach-form [type=submit]').click()
                    expect(page.locator('#new-sub-detach-modal')).to_be_hidden()
                    expect(page.locator('#new-sub-payment-method')).to_be_hidden()
                    expect(page.locator('#new-sub-detached')).to_be_visible()
                    expect(page.locator('#new-sub-detached')).to_contain_text('Автопродление отключено.')
                    self.assertEqual(page.url, 'http://testserver' + self.preview_url)
                    page.reload()
                    expect(page.locator('#new-sub-payment-method')).to_be_visible()
                    self.assertFalse(any(method != 'GET' for method, _ in requests))
                    self.assertFalse(any('/api/payment-method/detach/' in url for _, url in requests))
                finally:
                    browser.close()
            create.assert_not_called()
            find.assert_not_called()
            detach.assert_not_called()
        self.assertEqual(self.billing_snapshot(), before)
        self.assertFalse(settings.CLUB_YOOKASSA_ENABLED)
        self.assertFalse(settings.YOOKASSA_RECURRING_ENABLED)
