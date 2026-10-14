"""Enable the public Censorly launch with the homepage card release."""

from django.db import migrations


def make_censorly_public(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    game = Game.objects.filter(id='censorly').first()
    if game is None:
        return
    tags = dict(game.tags or {})
    if tags.get('censorly_public'):
        return
    tags['censorly_public'] = True
    game.tags = tags
    game.save(update_fields=['tags'])


def make_censorly_private(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    game = Game.objects.filter(id='censorly').first()
    if game is None:
        return
    tags = dict(game.tags or {})
    tags.pop('censorly_public', None)
    game.tags = tags
    game.save(update_fields=['tags'])


class Migration(migrations.Migration):
    dependencies = [('games', '0278_censorly_editable_taxonomy')]

    operations = [migrations.RunPython(make_censorly_public, make_censorly_private)]
