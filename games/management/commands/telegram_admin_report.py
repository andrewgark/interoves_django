from django.core.management.base import BaseCommand

from games.telegram.admin_reports import process_admin_report_tick
from games.cron_lock import distributed_cron_lock


class Command(BaseCommand):
    help = 'Send the daily or weekly Telegram activity report at 00:25 local time.'

    def handle(self, *args, **options):
        with distributed_cron_lock('telegram_admin_report', ttl_seconds=180) as acquired:
            if not acquired:
                self.stdout.write('telegram admin report skipped: lock held')
                return
            result = process_admin_report_tick()
        self.stdout.write('Admin report: sent={sent}, skipped={skipped}'.format(**result))
