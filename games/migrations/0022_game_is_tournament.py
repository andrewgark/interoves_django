# Generated by Django 2.1.5 on 2020-07-20 19:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0021_auto_20200720_2204'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='is_tournament',
            field=models.BooleanField(default=False),
        ),
    ]
