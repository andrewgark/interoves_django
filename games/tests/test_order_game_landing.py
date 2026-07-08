from django.contrib.sites.models import Site
from django.core import mail
from django.test import TestCase, override_settings

from allauth.socialaccount.models import SocialApp

from games.models import CorporateGameOrder, OrderGameClient, OrderGameReview


@override_settings(
    CORPORATE_ORDER_EMAIL='andrewgarkavyy@gmail.com',
    DEFAULT_FROM_EMAIL='test@interoves.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class OrderGameLandingTests(TestCase):
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

        OrderGameClient.objects.create(
            company_name='Pinely',
            logo_url='https://example.com/pinely.png',
            sort_order=1,
        )
        OrderGameReview.objects.create(
            name='Рома',
            caption='31 годик, оператор ЭВМ',
            text='Лучшая корпоративная игра за последние годы.',
            is_important=True,
        )

    def test_get_page(self):
        r = self.client.get('/order-game/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Заказать корпоративную игру')
        self.assertContains(r, 'Наши клиенты')
        self.assertContains(r, 'corp-reviews-carousel')
        self.assertContains(r, 'Способ связи')
        self.assertContains(r, 'id_contact_value')
        self.assertContains(r, 'Рома')
        self.assertNotContains(r, 'крупных IT-компаний')
        self.assertNotContains(r, 'Короткевич')

    def test_corporate_redirects_to_order_game(self):
        r = self.client.get('/corporate/')
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r['Location'], '/order-game/')

    def test_post_creates_order_and_sends_email(self):
        r = self.client.post('/order-game/', {
            'company_name': 'Test Corp',
            'contact_name': 'Alice',
            'contact_method': 'telegram',
            'contact_value': '@alice',
            'contact_other_label': '',
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
        self.assertEqual(order.contact_method, 'telegram')
        self.assertEqual(order.contact_value, '@alice')
        self.assertTrue(order.email_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Способ связи: Telegram', mail.outbox[0].body)
        self.assertIn('Контакт: @alice', mail.outbox[0].body)

    def test_other_contact_requires_label(self):
        r = self.client.post('/order-game/', {
            'company_name': 'Test Corp',
            'contact_name': 'Alice',
            'contact_method': 'other',
            'contact_value': '+7 900 000-00-00',
            'contact_other_label': '',
            'website': '',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Укажите тип контакта')
        self.assertEqual(CorporateGameOrder.objects.count(), 0)

    def test_other_contact_in_email(self):
        self.client.post('/order-game/', {
            'company_name': 'Test Corp',
            'contact_name': 'Alice',
            'contact_method': 'other',
            'contact_value': '+7 900 000-00-00',
            'contact_other_label': 'WhatsApp',
            'website': '',
        })
        self.assertIn('Способ связи: Другое (WhatsApp)', mail.outbox[0].body)

    def test_honeypot_rejects_spam(self):
        r = self.client.post('/order-game/', {
            'company_name': 'Spam Inc',
            'contact_name': 'Bot',
            'contact_method': 'email',
            'contact_value': 'bot@spam.com',
            'website': 'http://spam.example',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CorporateGameOrder.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_unpublished_client_and_review_are_hidden(self):
        OrderGameClient.objects.create(
            company_name='Hidden Corp',
            logo_url='https://example.com/hidden.png',
            is_published=False,
        )
        OrderGameReview.objects.create(
            name='Скрытый',
            caption='отзыв',
            text='Не должен показываться',
            is_published=False,
        )
        r = self.client.get('/order-game/')
        self.assertNotContains(r, 'Hidden Corp')
        self.assertNotContains(r, 'Скрытый отзыв')
