"""Перевести уже заведённые криптики на 3 балла и две подсказки.

Новые и пересохранённые в консоли дни получают это сами; команда делает то же
для всех дней раздела разом. Где баллы поменялись, попытки перепроверяются,
чтобы верные ответы стали стоить новых баллов.

Dry run by default; ``--apply`` writes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from games.daily.catalog.cryptic import CRYPTIC_GAME_ID
from games.models import GameTaskGroup, Task, TaskGroup
from games.puzzles.cryptic.explain import hint_htmls
from games.puzzles.cryptic.hints import CRYPTIC_POINTS, sync_cryptic_hints


class Command(BaseCommand):
    help = 'Криптики: 3 балла и две подсказки у всех дней раздела'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Записать изменения')

    def handle(self, *args, apply=False, **options):
        from games.ops.workers.jobs.recheck import recheck_full

        links = GameTaskGroup.objects.filter(game_id=CRYPTIC_GAME_ID).select_related('task_group')
        for link in links:
            tasks = Task.objects.filter(task_group_id=link.task_group_id, is_removed=False)
            for task in tasks:
                tags = task.tags or {}
                has_hints = hint_htmls(
                    clue=str(tags.get('cryptic_clue') or ''), answer=task.answer or '',
                    markup=str(tags.get('cryptic_markup') or ''),
                    formula=str(tags.get('cryptic_formula') or ''),
                ) is not None
                points_changed = task.points != CRYPTIC_POINTS
                self.stdout.write('{} task {}: points {} -> {}, hints: {}'.format(
                    link.number, task.pk, task.points, CRYPTIC_POINTS, 'yes' if has_hints else 'no',
                ))
                if not apply:
                    continue
                with transaction.atomic():
                    if points_changed:
                        Task.objects.filter(pk=task.pk).update(points=CRYPTIC_POINTS)
                        task.points = CRYPTIC_POINTS
                    sync_cryptic_hints(task)
                if points_changed:
                    recheck_full(None, task=task)
            if apply and link.task_group.points != CRYPTIC_POINTS:
                TaskGroup.objects.filter(pk=link.task_group_id).update(points=CRYPTIC_POINTS)
        if not apply:
            self.stdout.write('Dry run: добавьте --apply')
