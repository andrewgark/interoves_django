# Цензурки: future publish_start for daily schedule (not on public hub yet).

from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def set_future_publish_start(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    game = Game.objects.filter(id='censorly').first()
    if game is None:
        return
    tags = dict(game.tags or {})
    if tags.get('censorly_publish_start'):
        return
    start = (timezone.now() + timedelta(days=30)).astimezone(
        timezone.get_current_timezone()
    ).date()
    # Store as Moscow midnight ISO.
    tags['censorly_publish_start'] = f'{start.isoformat()}T00:00:00+03:00'
    game.tags = tags
    game.is_ready = True
    game.save(update_fields=['tags', 'is_ready'])


def clear_publish_start(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    game = Game.objects.filter(id='censorly').first()
    if game is None:
        return
    tags = dict(game.tags or {})
    tags.pop('censorly_publish_start', None)
    game.tags = tags
    game.save(update_fields=['tags'])


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0252_censorly_section_game'),
    ]

    operations = [
        migrations.RunPython(set_future_publish_start, clear_publish_start),
    ]
