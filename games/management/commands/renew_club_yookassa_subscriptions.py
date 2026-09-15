from django.core.management.base import BaseCommand

from games.club_yookassa import renew_due_subscriptions, yookassa_recurring_enabled


class Command(BaseCommand):
    help = 'Charge due YooKassa Club monthly renewals (requires YOOKASSA_RECURRING_ENABLED).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        if not yookassa_recurring_enabled():
            self.stdout.write('YOOKASSA_RECURRING_ENABLED=false — skip')
            return
        stats = renew_due_subscriptions(limit=options['limit'])
        self.stdout.write(
            'due={due} created={created} skipped={skipped} errors={errors}'.format(**stats)
        )
