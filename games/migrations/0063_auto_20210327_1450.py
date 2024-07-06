# Generated by Django 3.1.6 on 2021-03-27 11:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0062_game_requires_ticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='registration',
            name='time',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='task',
            name='tags',
            field=models.JSONField(default=dict),
        ),
        migrations.AlterField(
            model_name='attempt',
            name='time',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AlterField(
            model_name='task',
            name='task_type',
            field=models.CharField(choices=[('default', 'default'), ('wall', 'wall'), ('text_with_forms', 'text_with_forms'), ('distribute_to_teams', 'distribute_to_teams'), ('with_tag', 'with_tag')], default='default', max_length=100),
        ),
    ]
