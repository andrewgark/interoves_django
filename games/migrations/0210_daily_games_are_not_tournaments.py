from django.db import migrations


DAILY_GAME_IDS = ('ladder', 'alphabetty', 'salad')


def mark_daily_games_general(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    Game.objects.using(schema_editor.connection.alias).filter(
        pk__in=DAILY_GAME_IDS,
    ).update(is_tournament=False)


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0209_saved_payment_method'),
    ]

    operations = [
        migrations.RunPython(mark_daily_games_general, migrations.RunPython.noop),
    ]
