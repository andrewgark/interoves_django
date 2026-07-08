# Add raddle task type and checker.

from django.db import migrations, models


def add_raddle_checker(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    CheckerType.objects.get_or_create(id='raddle')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0120_game_taskgroup_is_18_plus'),
    ]

    operations = [
        migrations.RunPython(add_raddle_checker, noop),
        migrations.AlterField(
            model_name='task',
            name='task_type',
            field=models.CharField(
                choices=[
                    ('default', 'default'),
                    ('wall', 'wall'),
                    ('text_with_forms', 'text_with_forms'),
                    ('replacements_lines', 'replacements_lines'),
                    ('distribute_to_teams', 'distribute_to_teams'),
                    ('with_tag', 'with_tag'),
                    ('autohint', 'autohint'),
                    ('proportions', 'Пропорции'),
                    ('raddle', 'raddle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
    ]
