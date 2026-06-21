from django.contrib.sites.models import Site
from django.core import mail
from django.test import TestCase, override_settings

from allauth.socialaccount.models import SocialApp

from games.models import CorporateGameOrder


@override_settings(
    CORPORATE_ORDER_EMAIL='andrewgarkavyy@gmail.com',
    DEFAULT_FROM_EMAIL='test@interoves.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class CorporateLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
        for provider, name in (('google', 'Google'), ('vk', 'VK')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)

    def test_get_page(self):
        r = self.client.get('/corporate/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Заказать корпоративную игру')
        self.assertContains(r, 'Pinely')

    def test_post_creates_order_and_sends_email(self):
        r = self.client.post('/corporate/', {
            'company_name': 'Test Corp',
            'contact_name': 'Alice',
            'email': 'alice@testcorp.com',
            'phone': '+7 900 000-00-00',
            'team_size': '6',
            'preferred_date': 'Июнь 2026',
            'message': 'Хотим пазлхант',
            'website': '',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Заявка отправлена')
        self.assertEqual(CorporateGameOrder.objects.count(), 1)
        order = CorporateGameOrder.objects.get()
        self.assertEqual(order.company_name, 'Test Corp')
        self.assertTrue(order.email_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['andrewgarkavyy@gmail.com'])
        self.assertIn('Test Corp', mail.outbox[0].subject)

    def test_honeypot_rejects_spam(self):
        r = self.client.post('/corporate/', {
            'company_name': 'Spam Inc',
            'contact_name': 'Bot',
            'email': 'bot@spam.com',
            'website': 'http://spam.example',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CorporateGameOrder.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
