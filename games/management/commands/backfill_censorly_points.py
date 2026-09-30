"""Set Цензурки Task/TaskGroup points to the current base (20)."""

from django.core.management.base import BaseCommand

from games.censorly import CENSORLY_GAME_ID, CENSORLY_TASK_TYPE
from games.censorly.play import CENSORLY_BASE_POINTS
from games.models import GameTaskGroup, Task, TaskGroup


class Command(BaseCommand):
    help = 'Backfill censorly Task and TaskGroup points to {}.'.format(CENSORLY_BASE_POINTS)

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        tasks = Task.objects.filter(task_type=CENSORLY_TASK_TYPE).exclude(points=CENSORLY_BASE_POINTS)
        tg_ids = GameTaskGroup.objects.filter(game_id=CENSORLY_GAME_ID).values_list(
            'task_group_id', flat=True,
        )
        groups = TaskGroup.objects.filter(pk__in=tg_ids).exclude(points=CENSORLY_BASE_POINTS)
        self.stdout.write('Tasks to update: {}'.format(tasks.count()))
        self.stdout.write('TaskGroups to update: {}'.format(groups.count()))
        if dry:
            return
        updated_tasks = tasks.update(points=CENSORLY_BASE_POINTS)
        updated_groups = groups.update(points=CENSORLY_BASE_POINTS)
        self.stdout.write(self.style.SUCCESS(
            'Updated tasks={} task_groups={}'.format(updated_tasks, updated_groups),
        ))
