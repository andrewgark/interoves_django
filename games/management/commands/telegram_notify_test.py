from django.core.management.base import BaseCommand

from games.telegram.notify import send_admin_message, send_announce_message, telegram_notify_configured


class Command(BaseCommand):
    help = 'Send a test Telegram message to admin and announce chats.'

    def add_arguments(self, parser):
        parser.add_argument('--announce-only', action='store_true')
        parser.add_argument('--admin-only', action='store_true')

    def handle(self, *args, **options):
        if not telegram_notify_configured():
            self.stderr.write('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID first.')
            return

        text = '✅ Interoves Telegram bot test OK'
        if not options['announce_only']:
            ok = send_admin_message(text, force=True)
            self.stdout.write('Admin: {}'.format('sent' if ok else 'failed'))
        if not options['admin_only']:
            ok = send_announce_message(text)
            self.stdout.write('Announce: {}'.format('sent' if ok else 'skipped/failed'))
