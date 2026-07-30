from django.core.management.base import BaseCommand

from games.anon_migrate import heal_orphaned_chain_states_from_migrate_events


class Command(BaseCommand):
    help = (
        'Перенести ChainTaskState, оставшиеся на anon_key после anon_attempts_migrated '
        '(баг: migrate переносил только Attempt/HintAttempt).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только посчитать, сколько строк затронуто (без записи).',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            from games.models import ChainTaskState, StatisticsEvent
            from django.contrib.auth import get_user_model

            User = get_user_model()
            events = 0
            rows = 0
            for ev in StatisticsEvent.objects.filter(
                kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            ):
                payload = ev.payload or {}
                anon_key = payload.get('anon_key')
                if not anon_key or not ev.user_id:
                    continue
                if not User.objects.filter(pk=ev.user_id).exists():
                    continue
                n = ChainTaskState.objects.filter(
                    anon_key=anon_key, user__isnull=True, team__isnull=True,
                ).count()
                if n:
                    events += 1
                    rows += n
            self.stdout.write(
                'dry-run: {} migrate events still have orphan CTS ({} rows)'.format(
                    events, rows,
                )
            )
            return

        events, rows = heal_orphaned_chain_states_from_migrate_events()
        self.stdout.write(self.style.SUCCESS(
            'Healed {} migrate events, {} ChainTaskState rows'.format(events, rows)
        ))
