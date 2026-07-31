# Убрать блок «Архив» из правил Задания недели.

from django.db import migrations

WEEK_TASK_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Задание недели</h2>
<p class="pal-lead">
  Каждую неделю — одно особое задание из прошлых Десяточек.
  Новое открывается в понедельник в полночь по Москве.
</p>
'''


def update_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_week_task',
        defaults={'html': WEEK_TASK_TUTORIAL_HTML},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0152_alphabetty_tutorial_rules_text'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
