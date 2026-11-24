# Адвент-календарь по умолчанию называется New Year Hunt.

from django.db import migrations


def rename(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    Game.objects.filter(id='advent', name='Адвент-календарь').update(
        name='New Year Hunt', outside_name='New Year Hunt',
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0314_cryptic_section_game'),
    ]

    operations = [
        migrations.RunPython(rename, noop),
    ]
