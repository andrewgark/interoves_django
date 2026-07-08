# Обновляем баллы за подсказки в лесенках: 1 подсказка → 0.5, 2 подсказки → 0.

import json

from django.db import migrations


def forwards(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    for task in Task.objects.filter(task_type='raddle'):
        raw = (task.checker_data or '').strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        assist = data.get('raddle_assist')
        if not isinstance(assist, dict):
            assist = {}
        assist['fractions'] = [1, 0.5, 0]
        assist.setdefault('enabled', True)
        data['raddle_assist'] = assist
        task.checker_data = json.dumps(data, ensure_ascii=False)
        task.save(update_fields=['checker_data'])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0132_ladder_tutorial_examples'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
