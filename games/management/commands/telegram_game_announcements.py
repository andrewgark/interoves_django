from django.core.management.base import BaseCommand

from games.telegram.scheduling import process_game_announcements


class Command(BaseCommand):
    help = 'Send scheduled Telegram game announcements (chat mode) and admin start-soon reminders.'

    def handle(self, *args, **options):
        stats = process_game_announcements()
        self.stdout.write(
            'Announcements sent: day_before={day_before}, hour_before={hour_before}, '
            'start={start}, end_soon_15={end_soon_15}, end={end}, '
            'all_solved={all_solved}, results={results}, '
            'admin_start_soon={admin_start_soon}, ladder_scheduled={ladder_scheduled}'.format(
                ladder_scheduled=stats.get('ladder_scheduled', 0),
                **{k: stats.get(k, 0) for k in (
                    'day_before', 'hour_before', 'start', 'end_soon_15', 'end',
                    'all_solved', 'results', 'admin_start_soon',
                )},
            )
        )
