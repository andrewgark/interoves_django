from django.core.management.base import BaseCommand

from games.ops.cron.lock import distributed_cron_lock
from games.club.billing.gifts.offers import expire_subscription_gifts


class Command(BaseCommand):
    help = 'Expire paid or unpaid subscription gifts past their validity date.'

    def handle(self, *args, **options):
        with distributed_cron_lock('subscription_gifts_expire', ttl_seconds=300) as acquired:
            if not acquired:
                self.stdout.write('subscription gift expiry skipped: lock held')
                return
            count = expire_subscription_gifts()
        self.stdout.write(self.style.SUCCESS('Expired {} subscription gift(s).'.format(count)))
