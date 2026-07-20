"""Interactive one-time login to create a Telethon StringSession for channel scheduling."""

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Log in as a Telegram *user* (not the bot) and print a StringSession. '
        'Save it to secrets/telegram_user_session.txt / TELEGRAM_USER_SESSION. '
        'That account must be able to post to the channel (admin with Post Messages).'
    )

    def handle(self, *args, **options):
        api_id = getattr(settings, 'TELEGRAM_API_ID', 0) or 0
        api_hash = getattr(settings, 'TELEGRAM_API_HASH', '') or ''
        if not api_id or not api_hash:
            self.stderr.write(
                'Set TELEGRAM_API_ID and TELEGRAM_API_HASH first '
                '(from https://my.telegram.org → API development tools).\n'
                'Local: secrets/telegram_api_id.txt and secrets/telegram_api_hash.txt'
            )
            return

        self.stdout.write(
            'Logging in as a user account. Use the phone of a channel admin '
            '(бот сюда не подходит — schedule_date только для user session).'
        )
        me, session_str = asyncio.run(self._login(int(api_id), api_hash))

        self.stdout.write(self.style.SUCCESS(
            'Logged in as {} (id={})'.format(
                getattr(me, 'username', None) or getattr(me, 'first_name', ''),
                me.id,
            )
        ))
        self.stdout.write('')
        self.stdout.write('Save this entire string to secrets/telegram_user_session.txt')
        self.stdout.write('(or eb setenv TELEGRAM_USER_SESSION=...):')
        self.stdout.write('')
        self.stdout.write(session_str)
        self.stdout.write('')

        import os

        out_path = os.path.join(settings.BASE_DIR, 'secrets', 'telegram_user_session.txt')
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(session_str.strip() + '\n')
            self.stdout.write(self.style.SUCCESS('Also wrote {}'.format(out_path)))
        except OSError as exc:
            self.stderr.write('Could not write session file: {}'.format(exc))

    async def _login(self, api_id: int, api_hash: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.start()
        me = await client.get_me()
        session_str = client.session.save()
        await client.disconnect()
        return me, session_str
