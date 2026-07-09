from django.conf import settings
from django.core.management.base import BaseCommand

from games.telegram.api import delete_webhook, set_webhook
from games.telegram.game_urls import admin_url


class Command(BaseCommand):
    help = 'Register Telegram webhook URL for admin commands and callbacks.'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='Remove webhook (switch to getUpdates polling locally).')
        parser.add_argument('--url', default='', help='Override webhook URL (default: SITE_BASE_URL/telegram/webhook/<secret>/).')

    def handle(self, *args, **options):
        if options['delete']:
            ok = delete_webhook()
            self.stdout.write('deleteWebhook: {}'.format('ok' if ok else 'failed'))
            return

        secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or ''
        if secret:
            path = '/telegram/webhook/{}/'.format(secret)
        else:
            path = '/telegram/webhook/'
            self.stdout.write(self.style.WARNING('TELEGRAM_WEBHOOK_SECRET is empty — webhook URL is guessable.'))

        url = options['url'] or admin_url(path)
        ok = set_webhook(url, secret_token=secret)
        self.stdout.write('setWebhook {} → {}'.format(url, 'ok' if ok else 'failed'))
