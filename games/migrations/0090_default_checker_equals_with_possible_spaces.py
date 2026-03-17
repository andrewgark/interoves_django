# Set default checker to equals_with_possible_spaces for TaskGroup and Task.
# Depends on 0089 so that CheckerType exists.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0089_ensure_checker_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskgroup',
            name='checker',
            field=models.ForeignKey(
                blank=True,
                default='equals_with_possible_spaces',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='task_groups',
                to='games.checkertype',
            ),
        ),
        migrations.AlterField(
            model_name='task',
            name='checker',
            field=models.ForeignKey(
                blank=True,
                default='equals_with_possible_spaces',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tasks',
                to='games.checkertype',
            ),
        ),
    ]
