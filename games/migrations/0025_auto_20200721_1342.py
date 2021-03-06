# Generated by Django 2.1.5 on 2020-07-21 10:42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0024_auto_20200721_1257'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='is_playable',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='game',
            name='is_ready',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='game',
            name='is_testing',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='team',
            name='is_tester',
            field=models.BooleanField(default=False),
        ),
    ]
