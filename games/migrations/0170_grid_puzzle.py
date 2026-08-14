from django.db import migrations, models


def ensure_wall_checker(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    CheckerType.objects.get_or_create(id='wall-checker')


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0169_task_attempt_revision'),
    ]

    operations = [
        migrations.RunPython(ensure_wall_checker, migrations.RunPython.noop),
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
                    ('alphabetty', 'alphabetty'),
                    ('word_salad', 'Словесный Салат'),
                    ('grid-puzzle', 'Grid Puzzle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
    ]
