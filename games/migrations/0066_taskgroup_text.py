# Generated by Django 3.1.6 on 2021-05-09 13:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0065_game_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskgroup',
            name='text',
            field=models.TextField(blank=True, null=True),
        ),
    ]
