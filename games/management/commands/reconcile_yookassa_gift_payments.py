from django.core.management.base import BaseCommand

from games.cron_lock import distributed_cron_lock
from games.subscription_gift_payments import reconcile_yookassa_gift_payments


class Command(BaseCommand):
    help = 'Reconcile pending or manually held YooKassa subscription gift payments.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        with distributed_cron_lock('subscription_gifts_yookassa_reconcile', ttl_seconds=300) as acquired:
            if not acquired:
                self.stdout.write('YooKassa gift reconciliation skipped: lock held')
                return
            count = reconcile_yookassa_gift_payments(limit=max(1, options['limit']))
        self.stdout.write(self.style.SUCCESS('Reconciled {} YooKassa gift payment(s).'.format(count)))
