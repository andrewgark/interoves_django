# Project "sections" for Разделы hub tiles (one per game).
# Game "Палиндромы" lives here; add palindrome task groups to this game in admin.

from django.db import migrations


def create_sections_and_palindromes(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')
    game, _ = Game.objects.update_or_create(
        id='palindromes',
        defaults={
            'name': 'Палиндромы',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0087_ensure_social_apps'),
    ]

    operations = [
        migrations.RunPython(create_sections_and_palindromes, noop),
    ]
