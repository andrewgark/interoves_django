import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup


class Command(BaseCommand):
    help = 'Create a local demo game for the html_forms task type.'

    def handle(self, *args, **options):
        project, _ = Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        html_checker, _ = CheckerType.objects.get_or_create(pk='html_forms')

        game, _ = Game.objects.update_or_create(
            id='html_forms_demo',
            defaults={
                'project': project,
                'name': 'Demo: HTML forms chain task',
                'author': 'inter oves',
                'author_extra': '',
                'start_time': timezone.now() - timezone.timedelta(days=1),
                'end_time': timezone.now() + timezone.timedelta(days=30),
                'visible_start_time': timezone.now() - timezone.timedelta(days=1),
                'visible_end_time': timezone.now() + timezone.timedelta(days=30),
                'is_ready': True,
                'is_playable': True,
                'is_tournament': True,
                'is_registrable': False,
                'requires_ticket': False,
            },
        )
        group, _ = TaskGroup.objects.update_or_create(
            label='html_forms_demo_group',
            defaults={
                'points': 1,
                'max_attempts': 3,
            },
        )
        GameTaskGroup.objects.update_or_create(
            game=game,
            task_group=group,
            defaults={'number': '1', 'name': 'Одна HTML-задача, три формы'},
        )
        task, _ = Task.objects.update_or_create(
            task_group=group,
            number='1',
            defaults={
                'task_type': 'html_forms',
                'checker': html_checker,
                'points': 2,
                'max_attempts': 25,
                'field_text_width': 20,
                'text': (
                    '<p>Это одно задание с произвольным HTML. Ответы можно сдавать '
                    'в любом порядке; каждая форма дает 2 балла.</p>'
                    '<table class="table table-bordered" style="max-width:720px">'
                    '<thead><tr><th>#</th><th>Вопрос</th><th>Ответ</th></tr></thead>'
                    '<tbody>'
                    '<tr><td>1</td><td>Латинская первая буква</td><td>{{ html_form:alpha }}</td></tr>'
                    '<tr><td>2</td><td>Можно писать с пробелами и пунктуацией</td><td>{{ html_form:beta }}</td></tr>'
                    '<tr><td>3</td><td>У этой формы есть две принимаемые версии</td><td>{{ html_form:gamma }}</td></tr>'
                    '</tbody></table>'
                ),
                'checker_data': json.dumps({
                    'forms': [
                        {'key': 'alpha', 'label': 'A', 'answer': 'alpha', 'placeholder': 'alpha'},
                        {'key': 'beta', 'label': 'B', 'answer': 'new york', 'placeholder': 'new york'},
                        {'key': 'gamma', 'label': 'C', 'answers': ['gamma', 'гамма'], 'placeholder': 'gamma / гамма'},
                    ],
                }, ensure_ascii=False),
                'answer': 'alpha; new york; gamma',
            },
        )
        self.stdout.write(self.style.SUCCESS(
            'Demo is ready: /games/{}/1/ (task id {})'.format(game.id, task.id)
        ))
