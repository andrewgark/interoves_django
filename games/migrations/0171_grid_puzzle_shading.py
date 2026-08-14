from django.db import migrations


def add_grid_checkers(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    Task = apps.get_model('games', 'Task')
    TaskGroup = apps.get_model('games', 'TaskGroup')

    CheckerType.objects.get_or_create(id='grid-wall-checker')
    CheckerType.objects.get_or_create(id='grid-shading-checker')
    Task.objects.filter(checker_id='wall-checker').update(checker_id='grid-wall-checker')
    TaskGroup.objects.filter(checker_id='wall-checker').update(checker_id='grid-wall-checker')
    CheckerType.objects.filter(id='wall-checker').delete()


def restore_wall_checker(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    Task = apps.get_model('games', 'Task')
    TaskGroup = apps.get_model('games', 'TaskGroup')

    if (
        Task.objects.filter(checker_id='grid-shading-checker').exists()
        or TaskGroup.objects.filter(checker_id='grid-shading-checker').exists()
    ):
        raise RuntimeError(
            'Cannot reverse grid shading migration while grid-shading-checker is in use'
        )

    CheckerType.objects.get_or_create(id='wall-checker')
    Task.objects.filter(checker_id='grid-wall-checker').update(checker_id='wall-checker')
    TaskGroup.objects.filter(checker_id='grid-wall-checker').update(checker_id='wall-checker')
    CheckerType.objects.filter(
        id__in=['grid-wall-checker', 'grid-shading-checker']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0170_grid_puzzle'),
    ]

    operations = [
        migrations.RunPython(add_grid_checkers, restore_wall_checker),
    ]
