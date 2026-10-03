"""MySQL integration coverage for bulk schedule renumbering."""

from unittest import skipUnless

from django.db import connection, transaction
from django.test import TransactionTestCase

from games.models import Game, GameTaskGroup, HTMLPage, Project, TaskGroup
from games.support.services.schedule_links import renumber_links


@skipUnless(connection.vendor == 'mysql', 'requires an independent MySQL backend')
class ScheduleLinksMySQLIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def test_bulk_renumber_moves_111_to_90_without_unique_collision(self):
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        project, _ = Project.objects.get_or_create(pk='sections')
        game = Game.objects.create(
            pk='schedule-links-mysql-test',
            name='Schedule links MySQL test',
            author='test',
            project=project,
        )
        task_groups = [
            TaskGroup(label=f'mysql-test:{number}')
            for number in range(1, 112)
        ]
        TaskGroup.objects.bulk_create(task_groups)
        task_groups = list(
            TaskGroup.objects.filter(label__startswith='mysql-test:').order_by('id')
        )
        links = [
            GameTaskGroup(
                game=game,
                task_group=task_group,
                number=str(number),
                name=f'#{number}',
            )
            for number, task_group in enumerate(task_groups, start=1)
        ]
        GameTaskGroup.objects.bulk_create(links)
        links = list(
            GameTaskGroup.objects.filter(game=game).order_by('number')
        )

        ordered = links[:89] + [links[110]] + links[89:110]
        with transaction.atomic():
            renumber_links(ordered)

        rows = list(
            GameTaskGroup.order_queryset_by_number(
                GameTaskGroup.objects.filter(game=game),
            ).values_list('number', 'task_group_id')
        )
        self.assertEqual(
            [number for number, _task_group_id in rows],
            [str(i) for i in range(1, 112)],
        )
        self.assertEqual(rows[89][1], links[110].task_group_id)
        self.assertEqual(rows[90][1], links[89].task_group_id)
        self.assertEqual(rows[110][1], links[109].task_group_id)
