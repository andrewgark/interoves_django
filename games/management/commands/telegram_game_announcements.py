from django.core.management.base import BaseCommand

from games.telegram.scheduling import process_game_announcements


class Command(BaseCommand):
    help = 'Send scheduled Telegram game announcements (chat mode) and admin start-soon reminders.'

    def handle(self, *args, **options):
        stats = process_game_announcements()
        self.stdout.write(
            'Announcements sent: start={start}, end_soon_30={end_soon_30}, end={end}, admin_start_soon={admin_start_soon}'.format(
                **stats,
            )
        )
