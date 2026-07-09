from django.core.management.base import BaseCommand

from games.telegram.notify import fetch_recent_telegram_chat_ids


class Command(BaseCommand):
    help = 'List recent Telegram chat ids from bot getUpdates (for admin / announce setup).'

    def handle(self, *args, **options):
        chats = fetch_recent_telegram_chat_ids()
        if not chats:
            self.stdout.write(self.style.WARNING(
                'No chats found. Send /start to the bot (admin) or add it to a group and post a message.'
            ))
            return
        for chat in chats:
            label_parts = []
            if chat.get('title'):
                label_parts.append(chat['title'])
            if chat.get('username'):
                label_parts.append('@{}'.format(chat['username']))
            if chat.get('first_name'):
                label_parts.append(chat['first_name'])
            label = ' · '.join(label_parts) or 'chat'
            self.stdout.write('{}  type={}  chat_id={}'.format(
                label, chat.get('type'), chat.get('chat_id'),
            ))
