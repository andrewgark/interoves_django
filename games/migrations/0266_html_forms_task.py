from django.db import migrations, models


def ensure_checker_type(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    CheckerType.objects.get_or_create(id='html_forms')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0265_teaminvite'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='task_type',
            field=models.CharField(
                choices=[
                    ('default', 'default'),
                    ('wall', 'wall'),
                    ('text_with_forms', 'text_with_forms'),
                    ('html_forms', 'html_forms'),
                    ('replacements_lines', 'replacements_lines'),
                    ('distribute_to_teams', 'distribute_to_teams'),
                    ('with_tag', 'with_tag'),
                    ('autohint', 'autohint'),
                    ('proportions', 'Пропорции'),
                    ('raddle', 'raddle'),
                    ('alphabetty', 'alphabetty'),
                    ('word_salad', 'Салатик'),
                    ('censorly', 'Цензурка'),
                    ('grid-puzzle', 'Grid Puzzle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
        migrations.RunPython(ensure_checker_type, noop),
    ]
