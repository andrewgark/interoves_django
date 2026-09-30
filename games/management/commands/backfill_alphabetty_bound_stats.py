"""
Warm daily statistics cache for Алфавитка editions after bound-word aggregates ship.

Recomputes cached payloads (including «слово сверху/снизу») for every
alphabetty GameTaskGroup. Safe to run repeatedly.

Usage:
    python manage.py backfill_alphabetty_bound_stats
    python manage.py backfill_alphabetty_bound_stats --dry-run
"""
from django.core.management.base import BaseCommand

from games.daily.statistics import build_daily_statistics, invalidate_daily_statistics
from games.models import Game, GameTaskGroup


class Command(BaseCommand):
    help = (
        'Invalidate and rebuild cached daily statistics for all Алфавитка task groups '
        '(includes above/below bound word aggregates).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List task groups that would be refreshed without touching the cache.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        game = Game.objects.filter(id='alphabetty').first()
        if game is None:
            self.stdout.write(self.style.WARNING('Game alphabetty not found; nothing to do.'))
            return

        placements = GameTaskGroup.objects.filter(game=game).select_related('task_group').order_by('id')
        count = placements.count()
        if dry_run:
            for placement in placements.iterator():
                self.stdout.write(
                    '  [dry-run] would refresh game={} task_group={} number={}'.format(
                        game.id, placement.task_group_id, placement.number,
                    ),
                )
            self.stdout.write(self.style.SUCCESS(
                'Would refresh {} alphabetty daily statistics cache entr(y/ies).'.format(count),
            ))
            return

        refreshed = 0
        for placement in placements.iterator():
            invalidate_daily_statistics(game.id, placement.task_group_id)
            build_daily_statistics(game, placement.task_group)
            refreshed += 1

        self.stdout.write(self.style.SUCCESS(
            'Refreshed {} alphabetty daily statistics cache entr(y/ies).'.format(refreshed),
        ))
