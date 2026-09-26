"""Check and recover the daily-game difficulty queue."""

from django.core.management.base import BaseCommand

from games.difficulty_health import run_daily_difficulty_health_check


class Command(BaseCommand):
    help = 'Repair and verify the daily-game difficulty refresh queue.'

    def handle(self, *args, **options):
        result = run_daily_difficulty_health_check()
        status = result['status']
        if status == 'skipped_locked':
            self.stdout.write('daily difficulty health check skipped: lock held')
            return
        if status == 'skipped_recent':
            self.stdout.write('Hourly difficulty check already ran recently; skipping.')
            return
        if status == 'unhealthy':
            self.stderr.write(self.style.ERROR('Daily difficulty unhealthy: ' + result['detail']))
            return
        self.stdout.write(self.style.SUCCESS(
            'Daily difficulty healthy; recovery tick processed successfully.'
        ))
