# Generated by Django 2.1.5 on 2020-07-28 19:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0033_auto_20200728_2002'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='task_type',
            field=models.CharField(choices=[('default', 'default'), ('wall', 'wall')], default='default', max_length=100),
        ),
    ]
