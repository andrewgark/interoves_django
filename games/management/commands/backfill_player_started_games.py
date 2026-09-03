from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from games.analytics import analytics_game_kind, game_instance_id_for_task_group
from games.analytics_persistence import (
    create_or_reread_analytics_row,
    read_exact_analytics_row,
)
from games.models import Attempt, GameTaskGroup, PlayerStartedGame


class Command(BaseCommand):
    help = 'Backfill unique server-side game starts from the earliest persisted Attempt.'

    def add_arguments(self, parser):
        parser.add_argument('--since', help='Optional ISO datetime lower bound for Attempt.time.')
        parser.add_argument('--until', help='Optional ISO datetime upper bound for Attempt.time.')
        parser.add_argument('--dry-run', action='store_true')

    def _parse_bound(self, raw, label):
        if not raw:
            return None
        value = parse_datetime(raw)
        if value is None:
            raise CommandError('{} must be an ISO datetime'.format(label))
        return value

    def handle(self, *args, **options):
        since = self._parse_bound(options.get('since'), '--since')
        until = self._parse_bound(options.get('until'), '--until')
        dry_run = bool(options.get('dry_run'))

        qs = (
            Attempt.manager.filter(
                skip=False,
                game__isnull=False,
                task__isnull=False,
                task__task_group__isnull=False,
            )
            .exclude(team__isnull=True, user__isnull=True, anon_key__isnull=True)
            .select_related('game', 'task__task_group')
            .order_by('time', 'id')
        )
        if since is not None:
            qs = qs.filter(time__gte=since)
        if until is not None:
            qs = qs.filter(time__lt=until)

        public_ids = {
            (row.game_id, row.task_group_id): str(row.number or '')
            for row in GameTaskGroup.objects.only('game_id', 'task_group_id', 'number')
        }
        seen = set()
        created = 0
        existing = 0

        for attempt in qs.iterator(chunk_size=2000):
            if attempt.team_id:
                actor_key = ('team', attempt.team_id)
                actor = {'team_id': attempt.team_id, 'user_id': None, 'anon_key': None}
            elif attempt.user_id:
                actor_key = ('user', attempt.user_id)
                actor = {'team_id': None, 'user_id': attempt.user_id, 'anon_key': None}
            elif attempt.anon_key:
                actor_key = ('anon', attempt.anon_key)
                actor = {'team_id': None, 'user_id': None, 'anon_key': attempt.anon_key}
            else:
                continue

            task_group = attempt.task.task_group
            instance_id = game_instance_id_for_task_group(attempt.game, task_group)
            dedupe_key = actor_key + (instance_id,)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            lookup = dict(actor, game_instance_id=instance_id)
            canonical = read_exact_analytics_row(PlayerStartedGame, lookup)
            if canonical is not None:
                existing += 1
                continue
            if dry_run:
                created += 1
                continue

            row, was_created = create_or_reread_analytics_row(
                PlayerStartedGame,
                lookup=lookup,
                defaults={
                    'game': attempt.game,
                    'task_group': task_group,
                    'game_kind': analytics_game_kind(attempt.game),
                    'public_game_id': public_ids.get(
                        (attempt.game_id, task_group.id), str(task_group.id),
                    ),
                    'is_backfilled': True,
                },
            )
            if not was_created:
                existing += 1
                continue
            if attempt.time is not None:
                PlayerStartedGame.objects.filter(
                    pk=row.pk,
                    is_backfilled=True,
                    instrumentation_version__isnull=True,
                ).update(started_at=attempt.time)
            created += 1

        label = 'would create' if dry_run else 'created'
        self.stdout.write(self.style.SUCCESS(
            '{} {} start rows; {} already existed'.format(label, created, existing)
        ))
