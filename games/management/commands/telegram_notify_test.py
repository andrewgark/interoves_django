from django.conf import settings
from django.core.management.base import BaseCommand

from games.telegram.notify import send_telegram_message, telegram_notify_configured


class Command(BaseCommand):
    help = 'Send a test Telegram notification using the configured bot token and chat id.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--message',
            default='Interoves: тестовое уведомление из manage.py telegram_notify_test',
            help='Custom message text (plain text, not HTML).',
        )

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write(self.style.ERROR(
                'TELEGRAM_BOT_TOKEN is empty. Create a bot via @BotFather and set the token '
                'in secrets/telegram_bot_token.txt or TELEGRAM_BOT_TOKEN env var.'
            ))
            return
        if not settings.TELEGRAM_NOTIFY_CHAT_ID:
            self.stderr.write(self.style.ERROR(
                'TELEGRAM_NOTIFY_CHAT_ID is empty. Send /start to your bot, then run '
                'manage.py telegram_notify_chat_id'
            ))
            return

        message = options['message']
        if telegram_notify_configured() and send_telegram_message(message):
            self.stdout.write(self.style.SUCCESS(
                'Sent test message to chat id {}.'.format(settings.TELEGRAM_NOTIFY_CHAT_ID)
            ))
        else:
            self.stderr.write(self.style.ERROR('Failed to send test message. Check application logs.'))
