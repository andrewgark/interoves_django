from django.core.management.base import BaseCommand

from games.telegram.digest import build_daily_digest
from games.telegram.notify import send_admin_message, telegram_admin_configured
from games.ticket_service import build_stuck_tickets_alert


class Command(BaseCommand):
    help = 'Send daily digest to admin Telegram chat.'

    def handle(self, *args, **options):
        if not telegram_admin_configured():
            self.stderr.write('Telegram admin chat is not configured.')
            return
        ok = send_admin_message(build_daily_digest(), force=True)
        self.stdout.write('Digest: {}'.format('sent' if ok else 'failed'))
        alert = build_stuck_tickets_alert()
        if alert:
            alert_ok = send_admin_message(alert, force=True)
            self.stdout.write('Stuck tickets alert: {}'.format('sent' if alert_ok else 'failed'))
