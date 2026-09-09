from django.core.management.base import BaseCommand

from games.telegram.admin_reports import process_admin_report_tick


class Command(BaseCommand):
    help = 'Send the daily or weekly Telegram activity report at 00:25 local time.'

    def handle(self, *args, **options):
        result = process_admin_report_tick()
        self.stdout.write('Admin report: sent={sent}, skipped={skipped}'.format(**result))
