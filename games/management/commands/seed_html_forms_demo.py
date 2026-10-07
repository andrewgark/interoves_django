import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup


NODE_FORM_MASKS = {
    'G1': '_______а_', 'G2': '______', 'G3': '____', 'G4': '_____',
    'G5': '____', 'G6': '___', 'G7': '__ф_______', 'G8': '___',
    'G9': '______', 'G10': '_______', 'G11': '_________', 'G12': '______р',
    'Y1': 'Ц__А', 'Y2': 'В_____ж', 'Y3': 'Новокуз___кая', 'Y4': 'Я____б',
    'Y5': 'К_______я', 'Y6': 'Х__к', 'Y7': 'Твинд_к', 'Y8': 'Коронк_',
    'Y9': 'Б__ь__', 'Y10': 'С__ц', 'Y11': 'С______о', 'Y12': 'М_и_о',
    'Y13': 'Ка__юратор', 'Y14': 'О___а', 'Y15': 'Сфе__',
}


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
                'max_attempts': 25,
            },
        )
        GameTaskGroup.objects.update_or_create(
            game=game,
            task_group=group,
            defaults={'number': '1', 'name': 'Одна HTML-задача, 27 форм'},
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
                    '<p>Заполняй все узлы в любом порядке. За каждый правильный '
                    'ответ начисляется 2 балла.</p>'
                    '<link rel="stylesheet" href="/static/css/html_forms_graph_demo.css?v=layout-v18">'
                    '<div class="html-forms-graph-demo categorka-schema" '
                    'data-html-forms-graph-demo data-graph-schema="categorka-schema" '
                    'role="group" aria-label="Граф из зелёных и жёлтых узлов">'
                    + ''.join(
                        '<template data-graph-form="{0}">{{{{ html_form:{0} }}}}</template>'.format(key)
                        for key in NODE_FORM_MASKS
                    )
                    + '</div><script src="/static/js/html_forms_graph_demo.js?v=layout-v14" defer></script>'
                ),
                # Temporary local-only keys let the author exercise each embedded
                # form before the actual puzzle answer key has been provided.
                'checker_data': json.dumps({
                    'forms': [
                        {
                            'key': key,
                            'label': key,
                            'answer': 'DEMO-' + key,
                            'placeholder': mask.replace('_', '▪'),
                        }
                        for key, mask in NODE_FORM_MASKS.items()
                    ],
                }, ensure_ascii=False),
                'answer': '',
            },
        )
        self.stdout.write(self.style.SUCCESS(
            'Demo is ready: /games/{}/1/ (task id {})'.format(game.id, task.id)
        ))
