from django.conf import settings
from django.core.management.base import BaseCommand

from games.telegram.notify import fetch_recent_telegram_chat_ids


class Command(BaseCommand):
    help = (
        'Show chat ids from recent messages to the bot. '
        'Send /start to your bot in Telegram first, then run this command.'
    )

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write(self.style.ERROR(
                'TELEGRAM_BOT_TOKEN is empty. Create a bot via @BotFather first.'
            ))
            return

        try:
            chats = fetch_recent_telegram_chat_ids()
        except Exception as exc:
            self.stderr.write(self.style.ERROR('Telegram API request failed: {}'.format(exc)))
            return

        if not chats:
            self.stdout.write(
                'No recent messages found.\n'
                '1. Open Telegram and find your bot\n'
                '2. Press Start or send /start\n'
                '3. Run this command again'
            )
            return

        self.stdout.write('Recent chat ids:')
        for chat in chats:
            label_parts = []
            if chat.get('username'):
                label_parts.append('@{}'.format(chat['username']))
            if chat.get('first_name'):
                label_parts.append(chat['first_name'])
            if chat.get('last_name'):
                label_parts.append(chat['last_name'])
            if chat.get('title'):
                label_parts.append(chat['title'])
            label = ' '.join(label_parts) or '(no label)'
            self.stdout.write('  chat_id={} type={} — {}'.format(
                chat['chat_id'],
                chat.get('type') or '?',
                label,
            ))
        self.stdout.write('')
        self.stdout.write(
            'Put your personal chat id into secrets/telegram_notify_chat_id.txt '
            'or TELEGRAM_NOTIFY_CHAT_ID env var, then run manage.py telegram_notify_test.'
        )
