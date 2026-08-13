from django.core.management.base import BaseCommand

from games.anon_migrate import heal_orphaned_likes_from_migrate_events


class Command(BaseCommand):
    help = (
        'Перенести и схлопнуть Like, оставшиеся на anon_key после '
        'anon_attempts_migrated.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только посчитать, сколько анонимных строк затронуто.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            from games.models import Like, StatisticsEvent

            anon_keys = set(
                StatisticsEvent.objects.filter(
                    kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
                    user__isnull=False,
                ).values_list('payload__anon_key', flat=True)
            )
            anon_keys.discard(None)
            anon_keys.discard('')
            rows = Like.manager.filter(
                anon_key__in=anon_keys,
                user__isnull=True,
                team__isnull=True,
            ).count()
            self.stdout.write(
                'dry-run: {} orphan anonymous Like rows'.format(rows)
            )
            return

        events, rows = heal_orphaned_likes_from_migrate_events()
        self.stdout.write(self.style.SUCCESS(
            'Healed {} migrate events, {} Like rows'.format(events, rows)
        ))
