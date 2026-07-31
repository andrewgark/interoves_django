# Раздел «Задание недели» (еженедельная публикация по понедельникам МСК).

from django.db import migrations


WEEK_TASK_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Задание недели</h2>
<p class="pal-lead">
  Каждую неделю — одно особое задание из прошлых Десяточек.
  Новое открывается в понедельник в полночь по Москве.
</p>
'''


def add_week_task(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')

    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')

    HTMLPage.objects.update_or_create(
        name='section_tutorial_week_task',
        defaults={'html': WEEK_TASK_TUTORIAL_HTML},
    )

    Game.objects.update_or_create(
        id='week_task',
        defaults={
            'name': 'Задание недели',
            'outside_name': 'Задание недели',
            'theme': 'Одно особое задание из Десяточек каждую неделю',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {'week_task_publish_start': '2026-08-03T00:00:00+03:00'},
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0150_alphabetty_section_game'),
    ]

    operations = [
        migrations.RunPython(add_week_task, noop),
    ]
