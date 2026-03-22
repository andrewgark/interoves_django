# Task group view «proportions» + task type «proportions».

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0106_profile_join_accept_as_primary'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskgroup',
            name='view',
            field=models.CharField(
                choices=[
                    ('default', 'default'),
                    ('table-3-n', 'table-3-n'),
                    ('table-4-n', 'table-4-n'),
                    ('proportions', 'proportions'),
                ],
                default='default',
                max_length=100,
            ),
        ),
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
                    ('proportions', 'proportions'),
                ],
                default='default',
                max_length=100,
            ),
        ),
    ]
