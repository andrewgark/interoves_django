# Generated by Django 2.2.13 on 2020-10-28 07:02

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0051_audio_name'),
    ]

    operations = [
        migrations.RenameField(
            model_name='audio',
            old_name='name',
            new_name='title',
        ),
    ]
