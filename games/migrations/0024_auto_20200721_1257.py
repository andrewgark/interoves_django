# Generated by Django 2.1.5 on 2020-07-21 09:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0023_auto_20200721_1252'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attempt',
            name='attempts_infos',
            field=models.ManyToManyField(blank=True, related_name='attempts', to='games.AttemptsInfo'),
        ),
    ]
