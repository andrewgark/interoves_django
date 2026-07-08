# ladder: is_ready=True so see_game_preview works like other sections.

from django.db import migrations


def forwards(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    Game.objects.filter(id='ladder').update(
        is_ready=True,
        is_playable=True,
        is_tournament=False,
        requires_ticket=False,
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0125_ladder_section_game'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
