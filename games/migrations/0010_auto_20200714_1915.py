# Generated by Django 2.1.5 on 2020-07-14 16:15

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0009_auto_20200714_1333'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='checker',
            name='checker_type',
        ),
        migrations.AlterField(
            model_name='task',
            name='checker',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='games.CheckerType'),
        ),
        migrations.AlterField(
            model_name='taskgroup',
            name='checker',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='task_groups', to='games.CheckerType'),
        ),
        migrations.DeleteModel(
            name='Checker',
        ),
    ]
