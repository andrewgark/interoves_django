# Алфавитка: 10 баллов за слово, −1 за каждую буквенную подсказку.

from django.db import migrations


def bump_alphabetty_points(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    Attempt = apps.get_model('games', 'Attempt')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')

    tg_ids = list(
        GameTaskGroup.objects.filter(game_id='alphabetty')
        .values_list('task_group_id', flat=True)
    )
    if not tg_ids:
        Task.objects.filter(task_type='alphabetty').update(points=10)
        return

    TaskGroup.objects.filter(id__in=tg_ids).update(points=10)
    Task.objects.filter(task_group_id__in=tg_ids).update(points=10)
    # Старые Ok были с 1 баллом — поднимаем до базы; штраф подсказок
    # считается из ChainTaskState в AttemptsInfo.get_sum_hint_penalty.
    Attempt.manager.filter(
        task__task_group_id__in=tg_ids,
        status='Ok',
        points=1,
    ).update(points=10)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0160_alphabetty_tutorial_rules_numbered'),
    ]

    operations = [
        migrations.RunPython(bump_alphabetty_points, noop),
    ]
