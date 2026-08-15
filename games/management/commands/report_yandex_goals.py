from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from games.models import (
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    TicketRequest,
)


class Command(BaseCommand):
    help = 'Report server-created Yandex goals and callback acknowledgement coverage.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)

    def handle(self, *args, **options):
        days = max(1, int(options['days']))
        since = timezone.now() - timedelta(days=days)

        starts = PlayerStartedGame.objects.filter(
            started_at__gte=since,
            is_backfilled=False,
        )
        completions = PlayerCompletedGame.objects.filter(
            completed_at__gte=since,
            is_backfilled=False,
        )
        signups = PlayerAnalyticsState.objects.filter(signup_at__gte=since)
        activations = PlayerAnalyticsState.objects.filter(
            activated_at__gte=since,
            activation_is_backfilled=False,
        )
        checkouts = TicketRequest.objects.filter(time__gte=since).filter(
            Q(yookassa_id__isnull=False) | Q(nowpayments_id__isnull=False)
        )
        purchases = TicketRequest.objects.filter(time__gte=since, status='Accepted')

        rows = (
            ('game_start', starts.count(), starts.filter(metrika_acked_at__isnull=False).count()),
            ('game_complete', completions.count(), completions.filter(metrika_acked_at__isnull=False).count()),
            ('signup', signups.count(), signups.filter(signup_goal_acked_at__isnull=False).count()),
            (
                'activated_player',
                activations.count(),
                activations.filter(activation_goal_acked_at__isnull=False).count(),
            ),
            (
                'ticket_checkout',
                checkouts.count(),
                checkouts.filter(checkout_goal_acked_at__isnull=False).count(),
            ),
            (
                'ticket_purchase',
                purchases.count(),
                purchases.filter(purchase_goal_sent_at__isnull=False).count(),
            ),
        )
        self.stdout.write('goal\tserver_created\tmetrika_acked\tcoverage')
        for goal, created, acked in rows:
            coverage = '{:.1f}%'.format(100 * acked / created) if created else '—'
            self.stdout.write('{}\t{}\t{}\t{}'.format(goal, created, acked, coverage))
